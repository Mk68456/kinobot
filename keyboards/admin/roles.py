from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def buttons(rows):
    markup = InlineKeyboardMarkup(row_width=1)
    for title, callback in rows:
        markup.add(InlineKeyboardButton(title, callback_data=callback))
    return markup


def roles_page(roles, page, has_more, user_id=None):
    rows = [
        (name, f"role:assign:{role_id}" if user_id else f"role:view:{role_id}")
        for role_id, name, bypass in roles
    ]
    prefix = "pickpage" if user_id else "page"
    if page:
        rows.append(("⬅️ Предыдущая", f"role:{prefix}:{page - 1}"))
    if has_more:
        rows.append(("Следующая ➡️", f"role:{prefix}:{page + 1}"))
    if user_id:
        rows.append(("Снять роль", "role:assign:0"))
    else:
        rows.extend([("➕ Создать роль", "role:create"), ("👤 Роль пользователя", "role:user")])
    rows.append(("⬅️ Назад", "role:home" if user_id else "role:exit"))
    return buttons(rows)


def role_card(role_id, bypass):
    return buttons(
        [
            ("✏️ Переименовать", f"role:rename:{role_id}"),
            (
                "Включить проверку подписки" if bypass else "Освободить от подписки",
                f"role:bypass:{role_id}:{0 if bypass else 1}",
            ),
            ("🗑 Удалить роль", f"role:delete:{role_id}"),
            ("⬅️ К списку", "role:home"),
        ]
    )
