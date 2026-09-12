from database.connection import database
from database.movies import add_new_movie
from database.categories import add_movie_category, add_movie_subcategory


def save_movie_draft(data):
    with database.transaction():
        categories = data.get("categories", [])
        existing_movie_number = data.get("existing_movie_number")

        if existing_movie_number:
            movie_number = existing_movie_number
            result_message = "Категории обновлены ✅"
        else:
            title = data.get("movie_title")
            poster_image = data.get("poster_image")
            card_description = data.get("card_description")
            movie_file = data.get("movie_file")
            movie_file_type = data.get("movie_file_type")
            movie_trailer = data.get("movie_trailer")
            content_type = data.get("content_type", "movie")
            card_style = "card" if poster_image else "simple"
            movie_number = add_new_movie(
                title,
                poster_image=poster_image,
                card_description=card_description,
                card_style=card_style,
                movie_file=movie_file,
                movie_file_type=movie_file_type,
                movie_trailer=movie_trailer,
                content_type=content_type,
            )
            result_message = "Успешно добавлено !"

        for category in categories:
            category_id = add_movie_category(movie_number, category["name"], category.get("season_number"))
            for sub in category.get("subcategories", []):
                add_movie_subcategory(category_id, sub["name"], sub["file_id"], sub["file_type"])

        return movie_number, result_message
