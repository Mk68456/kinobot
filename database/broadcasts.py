from database.connection import database


def create_job(source_chat_id, source_message_id, admin_id):
    with database.transaction():
        job_id = database.execute(
            """INSERT INTO BroadcastJobs(source_chat_id,source_message_id,admin_id)
            VALUES (?,?,?)""",
            (source_chat_id, source_message_id, admin_id),
        ).lastrowid
        database.execute(
            """INSERT INTO BroadcastRecipients(job_id,user_id)
            SELECT ?,id FROM Users WHERE is_active=1 GROUP BY id""",
            (job_id,),
        )
        return job_id


def list_jobs():
    return database.execute("SELECT id,status FROM BroadcastJobs ORDER BY id DESC LIMIT 10").fetchall()


def job_counts(job_id):
    return dict(
        database.execute(
            "SELECT status,COUNT(*) FROM BroadcastRecipients WHERE job_id=? GROUP BY status", (job_id,)
        ).fetchall()
    )


def get_job(job_id):
    return database.execute(
        "SELECT id,source_chat_id,source_message_id,admin_id,status FROM BroadcastJobs WHERE id=?", (job_id,)
    ).fetchone()


def cancel_job(job_id):
    with database.transaction():
        database.execute(
            "UPDATE BroadcastJobs SET status='cancelled' WHERE id=? AND status IN ('pending','running','paused')",
            (job_id,),
        )


def retry_job(job_id):
    with database.transaction():
        job = get_job(job_id)
        if not job or job[4] not in ("completed", "paused"):
            return False
        database.execute(
            "UPDATE BroadcastRecipients SET status='pending',error=NULL WHERE job_id=? AND status='failed'",
            (job_id,),
        )
        database.execute("UPDATE BroadcastJobs SET status='pending' WHERE id=?", (job_id,))
        return True
