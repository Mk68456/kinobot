from database.connection import database


def list_roles(offset=0, limit=10):
    return database.execute(
        "SELECT id,name,bypass_subscription FROM Roles ORDER BY id LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()


def get_role(role_id):
    return database.execute("SELECT id,name,bypass_subscription FROM Roles WHERE id=?", (role_id,)).fetchone()


def save_role(name, role_id=None):
    name = name.strip()
    if not 1 <= len(name) <= 48:
        raise ValueError("Название должно содержать от 1 до 48 символов.")
    with database.transaction():
        if role_id is None:
            return database.execute("INSERT INTO Roles(name) VALUES (?)", (name,)).lastrowid
        if not get_role(role_id):
            raise ValueError("Роль уже удалена.")
        database.execute("UPDATE Roles SET name=? WHERE id=?", (name, role_id))
        return role_id


def set_bypass(role_id, enabled):
    with database.transaction():
        database.execute("UPDATE Roles SET bypass_subscription=? WHERE id=?", (int(enabled), role_id))


def delete_role(role_id):
    with database.transaction():
        database.execute("DELETE FROM Roles WHERE id=?", (role_id,))


def assigned_count(role_id):
    return database.execute("SELECT COUNT(*) FROM UserRoles WHERE role_id=?", (role_id,)).fetchone()[0]


def user_role(user_id):
    return database.execute(
        """SELECT r.id,r.name,r.bypass_subscription FROM UserRoles ur
        JOIN Roles r ON r.id=ur.role_id WHERE ur.user_id=?""",
        (user_id,),
    ).fetchone()


def assign_role(user_id, role_id):
    if not database.execute("SELECT 1 FROM Users WHERE id=?", (user_id,)).fetchone():
        raise ValueError("Пользователь не найден. Сначала он должен запустить бота командой /start.")
    with database.transaction():
        if role_id is None:
            database.execute("DELETE FROM UserRoles WHERE user_id=?", (user_id,))
        else:
            if not get_role(role_id):
                raise ValueError("Роль уже удалена.")
            database.execute(
                "INSERT INTO UserRoles(user_id,role_id) VALUES (?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET role_id=excluded.role_id",
                (user_id, role_id),
            )


def bypasses_subscription(user_id):
    role = user_role(user_id)
    return bool(role and role[2])
