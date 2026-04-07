import asyncio
import asyncpg
import os

async def main():
    # Use the public IP from the gcloud check
    db_url = "postgresql://postgres:postgres@34.77.93.209:5432/postgres"
    print(f"Connecting to {db_url}...")
    try:
        conn = await asyncpg.connect(db_url)
        print("Connected successfully!")
        
        # Check if visit_activities table exists
        exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'visit_activities'
            );
        """)
        print(f"Table 'visit_activities' exists: {exists}")
        
        # Check if actual_date column exists in patient_visits
        col_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.columns 
                WHERE table_name = 'patient_visits' AND column_name = 'actual_date'
            );
        """)
        print(f"Column 'actual_date' in 'patient_visits' exists: {col_exists}")
        
        await conn.close()
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
