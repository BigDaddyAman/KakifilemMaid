import asyncpg
import os
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

# Database credentials from environment variables
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASSWORD")
DB_PORT = os.getenv("DB_PORT")

async def connect_db():
    """Connect to PostgreSQL."""
    try:
        return await asyncpg.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            port=DB_PORT
        )
    except Exception as e:
        logging.error(f"Database connection error: {e}")
        logging.debug(f"Connection params: host={DB_HOST}, db={DB_NAME}, user={DB_USER}, port={DB_PORT}")
        raise

async def init_db():
    """Initialize database tables."""
    conn = await connect_db()
    try:
        # Drop mutes table if exists to recreate with correct schema
        await conn.execute("DROP TABLE IF EXISTS mutes;")
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                messages_sent INT DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS pending_links (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                link TEXT,
                original_message TEXT,
                approved BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE mutes (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                muted_by BIGINT,
                duration_minutes INT,
                reason TEXT,
                muted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT TRUE,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS bans (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                banned_by BIGINT,
                reason TEXT,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT TRUE,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
        """)
    finally:
        await conn.close()

async def add_user(user_id, username):
    """Add or update user in database."""
    conn = await connect_db()
    await conn.execute("""
        INSERT INTO users (user_id, username, messages_sent) 
        VALUES ($1, $2, 1)
        ON CONFLICT (user_id) DO UPDATE 
        SET messages_sent = users.messages_sent + 1;
    """, user_id, username)
    await conn.close()

async def add_pending_link(user_id: int, link: str, original_message: str):
    """Add a new pending link for approval."""
    conn = await connect_db()
    try:
        row = await conn.fetchrow("""
            INSERT INTO pending_links (user_id, link, original_message)
            VALUES ($1, $2, $3)
            RETURNING id;
        """, user_id, link, original_message)
        return row['id']
    finally:
        await conn.close()

async def approve_link(link_id: int):
    """Approve a pending link and return its data."""
    conn = await connect_db()
    try:
        row = await conn.fetchrow("""
            SELECT p.link, p.original_message, u.username
            FROM pending_links p
            JOIN users u ON u.user_id = p.user_id
            WHERE p.id = $1
        """, link_id)
        
        await conn.execute("""
            UPDATE pending_links 
            SET approved = TRUE 
            WHERE id = $1
        """, link_id)
        
        return {
            'link': row['link'],
            'message': row['original_message'],
            'username': row['username']
        }
    finally:
        await conn.close()

async def reject_link(link_id: int):
    """Reject and delete a pending link."""
    conn = await connect_db()
    try:
        await conn.execute("""
            DELETE FROM pending_links
            WHERE id = $1;
        """, link_id)
    finally:
        await conn.close()

async def get_pending_links():
    """Get all pending links."""
    conn = await connect_db()
    rows = await conn.fetch("SELECT id, link FROM pending_links WHERE approved = FALSE;")
    await conn.close()
    return rows

async def get_user_id_from_username(username: str) -> int:
    """Get user_id from username."""
    conn = await connect_db()
    try:
        row = await conn.fetchrow("""
            SELECT user_id FROM users
            WHERE username = $1
        """, username)
        return row['user_id'] if row else None
    finally:
        await conn.close()

async def get_user_by_username(username: str) -> dict:
    """Get user details by username."""
    conn = await connect_db()
    try:
        row = await conn.fetchrow("""
            SELECT user_id, username
            FROM users
            WHERE username = $1
        """, username)
        if row:
            return {
                'user_id': row['user_id'],
                'username': row['username']
            }
        return None
    finally:
        await conn.close()

async def add_mute(user_id: int, muted_by: int, duration: int, reason: str = None):
    """Add a new mute record."""
    conn = await connect_db()
    try:
        # Ensure user exists
        await conn.execute("""
            INSERT INTO users (user_id) 
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id)
        
        # Add mute record
        await conn.execute("""
            INSERT INTO mutes (user_id, muted_by, duration_minutes, reason)
            VALUES ($1, $2, $3, $4)
        """, user_id, muted_by, duration, reason)
    finally:
        await conn.close()

async def remove_mute(user_id: int):
    """Remove active mute for user."""
    conn = await connect_db()
    try:
        await conn.execute("""
            UPDATE mutes 
            SET active = FALSE 
            WHERE user_id = $1 AND active = TRUE
        """, user_id)
    finally:
        await conn.close()

async def add_ban(user_id: int, banned_by: int, reason: str = None):
    """Add a ban record."""
    conn = await connect_db()
    try:
        # Ensure user exists
        await conn.execute("""
            INSERT INTO users (user_id) 
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id)
        
        await conn.execute("""
            INSERT INTO bans (user_id, banned_by, reason)
            VALUES ($1, $2, $3)
        """, user_id, banned_by, reason)
    finally:
        await conn.close()

async def remove_ban(user_id: int):
    """Remove active ban for user."""
    conn = await connect_db()
    try:
        await conn.execute("""
            UPDATE bans 
            SET active = FALSE 
            WHERE user_id = $1 AND active = TRUE
        """, user_id)
    finally:
        await conn.close()
