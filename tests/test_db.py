from database import get_db_connection

try:
    conn = get_db_connection()
    print("✅ Successfully connected to the college_social_media database!")
    conn.close()
except Exception as e:
    print(f"❌ Connection failed: {e}")