import os
import datetime
import jwt
from typing import Literal

from fastapi import FastAPI, Depends, HTTPException, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from passlib.context import CryptContext

from database import (
    execute_query,
    execute_query_on_shard,
    execute_query_all_shards,
    execute_transaction,
    execute_transaction_on_shard,
    is_distributed,
)
from shard_router import get_shard_id

app = FastAPI()

# ---------- CONFIG ----------

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret")
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------- MODELS ----------

class LoginRequest(BaseModel):
    username: str
    password: str

class PostCreate(BaseModel):
    content: str
    visibility: Literal["Public", "Followers", "Private"] = "Public"

# ---------- AUTH ----------

def verify_token(token: str = Header(None, alias="session-token")):
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

def _hash_verify(p, h):
    return pwd_context.verify(p, h)

# ---------- DB ABSTRACTIONS (IMPORTANT) ----------

def _query_by_member(member_id, query, params=None, *, fetchone=False, fetchall=False):
    if is_distributed():
        return execute_query_on_shard(
            get_shard_id(member_id), query, params,
            fetchone=fetchone, fetchall=fetchall
        )
    return execute_query(query, params, fetchone=fetchone, fetchall=fetchall)

def _transaction_by_member(member_id, fn):
    if is_distributed():
        return execute_transaction_on_shard(get_shard_id(member_id), fn)
    return execute_transaction(fn)

# ---------- ROUTES ----------

@app.get("/health")
def health():
    return {"status": "ok"}

# ---------- AUTH ----------

@app.post("/login")
def login(req: LoginRequest):
    query = """
    SELECT m.MemberID, m.Email, m.Role, m.Name, a.PasswordHash
    FROM Member m
    JOIN AuthCredential a ON m.MemberID = a.MemberID
    WHERE m.Email = %s
    """

    user = None

    if is_distributed():
        # search across shards
        for sid in range(3):
            user = execute_query_on_shard(sid, query, (req.username,), fetchone=True)
            if user:
                break
    else:
        user = execute_query(query, (req.username,), fetchone=True)

    if not user or not _hash_verify(req.password, user["PasswordHash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = jwt.encode({
        "member_id": user["MemberID"],
        "role": user["Role"],
        "exp": int((datetime.datetime.utcnow() + datetime.timedelta(hours=1)).timestamp())
    }, SECRET_KEY, algorithm=ALGORITHM)

    return {"token": token}

# ---------- POSTS ----------

@app.post("/posts")
def create_post(data: PostCreate, user=Depends(verify_token)):
    member_id = user["member_id"]

    def tx(cursor):
        cursor.execute(
            """
            INSERT INTO Post (MemberID, Content, Visibility)
            VALUES (%s, %s, %s)
            """,
            (member_id, data.content, data.visibility)
        )
        return cursor.lastrowid

    post_id = _transaction_by_member(member_id, tx)

    return {"post_id": post_id}

@app.get("/posts")
def list_posts(user=Depends(verify_token), limit: int = 20):
    member_id = user["member_id"]

    query = """
        SELECT PostID, MemberID, Content, PostDate, Visibility
        FROM Post
        WHERE IsActive = TRUE
    """

    if is_distributed():
        posts = execute_query_all_shards(query)
        posts.sort(key=lambda x: x["PostDate"], reverse=True)
        posts = posts[:limit]
    else:
        posts = execute_query(query + " LIMIT %s", (limit,), fetchall=True)

    return {"data": posts}

# ---------- LIKE (CONCURRENCY SAFE) ----------

@app.post("/posts/{post_id}/like")
def like_post(post_id: int, user=Depends(verify_token)):
    member_id = user["member_id"]

    def tx(cursor):
        # lock row (critical for concurrency correctness)
        cursor.execute(
            "SELECT PostID FROM Post WHERE PostID=%s FOR UPDATE",
            (post_id,)
        )

        cursor.execute(
            """
            SELECT LikeID FROM `Like`
            WHERE MemberID=%s AND TargetID=%s
            FOR UPDATE
            """,
            (member_id, post_id)
        )

        existing = cursor.fetchone()

        if existing:
            cursor.execute("DELETE FROM `Like` WHERE LikeID=%s", (existing["LikeID"],))
            cursor.execute("UPDATE Post SET LikeCount=LikeCount-1 WHERE PostID=%s", (post_id,))
            return {"liked": False}
        else:
            cursor.execute(
                "INSERT INTO `Like` (MemberID, TargetType, TargetID) VALUES (%s,'Post',%s)",
                (member_id, post_id)
            )
            cursor.execute("UPDATE Post SET LikeCount=LikeCount+1 WHERE PostID=%s", (post_id,))
            return {"liked": True}

    return _transaction_by_member(member_id, tx)
