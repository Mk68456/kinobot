from tests.age_fixtures import reviewed_import, reviewed_group
import contextlib
import io
import unittest
from database.connection import database
from database.migrations import migrate
from database.movies import add_new_movie
from webpanel import repository
from services import movie_sharing


class AdminMergeTests(unittest.TestCase):
    def setUp(self):
        database.open(':memory:')
        with contextlib.redirect_stdout(io.StringIO()):migrate()

    def tearDown(self):database.close()

    def source(self,title):
        d=reviewed_import(99,{'version':1,'root':'D:/'+title,'title':title,'files':[{'path':'file.mp4','size':123}]})
        database.execute("UPDATE WebAssets SET status='ready',file_id='ready' WHERE id=?",(d['body']['items'][0]['asset_id'],))
        database.commit()
        return d

    def selections(self, drafts):
        return [{'draft_id':d['id'],'asset_id':d['body']['items'][0]['asset_id'],'review_token':repository.review_token(d)} for d in drafts]

    def test_group_seasons_and_keep_original_import_manifest(self):
        drafts=[self.source('A'),self.source('B')]
        result=reviewed_group(99,self.selections(drafts),{'title':'Series','content_type':'series'})
        body=result['body']
        for index,item in enumerate(body['items'],1):item.update(season_number=index,name='Episode 1')
        repository.save_draft(99,body,result['id'])
        number=repository.publish(result['id'],99)
        self.assertEqual(database.execute('SELECT season_number FROM MovieCategories WHERE movie_number=? ORDER BY season_number',(number,)).fetchall(),[(1,),(2,)])
        for old in drafts:
            source=repository.draft(old['id'],99)
            self.assertEqual(source['body'],old['body'])
            with self.assertRaises(ValueError):repository.publish(old['id'],99)
        self.assertEqual(database.execute('SELECT count(*) FROM MovieSubcategories').fetchone()[0],2)

    def test_group_validates_ownership_and_stale_selection(self):
        draft=self.source('A');selected=self.selections([draft]);body={'title':'Movie','content_type':'movie'}
        with self.assertRaises(ValueError):reviewed_group(100,selected,body)
        repository.save_draft(99,dict(draft['body'],title='Changed'),draft['id'])
        with self.assertRaises(ValueError):reviewed_group(99,selected,body)
        self.assertEqual(database.execute('SELECT count(*) FROM GroupedFiles').fetchone()[0],0)

    def test_group_keeps_source_seasons_quality_and_voice(self):
        drafts = [self.source('Season 2'), self.source('Season 3')]
        for season, source in enumerate(drafts, 2):
            body = source['body']
            body.update(content_type='series', catalog_section='series', season_number=season)
            body['items'][0].update(name='Episode 1', quality='720p', voice='rus')
            drafts[season-2] = repository.save_draft(99, body, source['id'])
        result = reviewed_group(99, self.selections(drafts), {'title':'Series', 'content_type':'series'})
        self.assertEqual([i['season_number'] for i in result['body']['items']], [2, 3])
        self.assertTrue(all(i['quality']=='720p' and i['voice']=='rus' for i in result['body']['items']))
        number = repository.publish(result['id'], 99)
        self.assertEqual(database.execute('SELECT season_number FROM MovieCategories WHERE movie_number=? ORDER BY season_number', (number,)).fetchall(), [(2,), (3,)])

    def test_renumber_preserves_links_votes_torrents_and_sequence(self):
        from database.torrents import add_movie_torrent
        from database.categories import add_movie_category
        number=add_new_movie('First')
        category=add_movie_category(number,'Dub')
        add_movie_torrent(number,'Torrent','file')
        token=movie_sharing.share_token(number);movie_sharing.accept(123,'share_'+token)
        database.execute('INSERT INTO FilmProgress(user_id,movie_number) VALUES (?,?)',(123,number));database.commit()
        repository.renumber_movie(99,str(number),500)
        self.assertEqual(movie_sharing.accept(456,'share_'+token),500)
        self.assertTrue(movie_sharing.permits(123,'dlfile_500'))
        self.assertEqual(database.execute('SELECT movie_number FROM MovieCategories WHERE id=?',(category,)).fetchone()[0],500)
        self.assertEqual(database.execute('SELECT movie_number FROM MovieTorrents').fetchone()[0],500)
        self.assertEqual(database.execute('SELECT movie_number FROM FilmProgress').fetchone()[0],500)
        self.assertGreater(add_new_movie('Next'),500)
        with self.assertRaises(ValueError):repository.renumber_movie(99,500,501)
        with self.assertRaises(ValueError):repository.renumber_movie(99,500,4.5)
        self.assertEqual(database.execute('SELECT movie_number FROM FilmProgress').fetchone()[0],500)

    def test_catalog_folder_rename_preserves_files_and_rejects_duplicates(self):
        from database.categories import add_movie_category, add_movie_subcategory

        number = add_new_movie('Film')
        first = add_movie_category(number, '1080p')
        second = add_movie_category(number, '720p')
        file_id = add_movie_subcategory(first, 'Озвучка', 'telegram-file', 'video')
        other_file_id = add_movie_subcategory(first, 'Субтитры', 'telegram-file-2', 'video')

        result = repository.rename_catalog_folders(number, [
            {'id': first, 'name': 'Full HD', 'files': [
                {'id': file_id, 'name': 'Дубляж'},
            ]},
            {'id': second, 'name': 'HD'},
        ])

        self.assertEqual([folder['name'] for folder in result['folders']], ['Full HD', 'HD'])
        self.assertEqual(result['folders'][0]['files'], [
            {'id': file_id, 'name': 'Дубляж'},
            {'id': other_file_id, 'name': 'Субтитры'},
        ])
        self.assertEqual(
            database.execute('SELECT file_id FROM MovieSubcategories WHERE id=?', (file_id,)).fetchone()[0],
            'telegram-file',
        )
        merged = repository.rename_catalog_folders(number, [{'id': second, 'name': 'full hd'}])
        self.assertEqual(merged['merged_folders'], 1)
        self.assertEqual(len(merged['folders']), 1)
        self.assertEqual({item['id'] for item in merged['folders'][0]['files']}, {file_id, other_file_id})
        with self.assertRaises(ValueError):
            repository.rename_catalog_folders(number, [{'id': 99999, 'name': 'Чужая папка'}])
        with self.assertRaises(ValueError):
            repository.rename_catalog_folders(number, [{
                'id': first, 'name': 'Full HD', 'files': [{'id': 99999, 'name': 'Чужой файл'}],
            }])
        with self.assertRaises(ValueError):
            repository.rename_catalog_folders(number, [{
                'id': first, 'name': 'Full HD', 'files': [{'id': file_id, 'name': 'субтитры'}],
            }])
        conflict = add_movie_category(number, 'Другая папка')
        add_movie_subcategory(conflict, 'Дубляж', 'telegram-file-3', 'video')
        with self.assertRaisesRegex(ValueError, 'объединяемых папках'):
            repository.rename_catalog_folders(number, [{'id': conflict, 'name': 'FULL HD'}])

    def test_removed_group_file_returns_to_source(self):
        drafts=[self.source('A'),self.source('B')]
        target=reviewed_group(99,self.selections(drafts),{'title':'Movie','content_type':'movie'})
        target['body']['items'].pop()
        repository.save_draft(99,target['body'],target['id'])
        self.assertEqual(repository.draft(drafts[1]['id'],99)['status'],'draft')
        self.assertEqual(repository.draft(drafts[0]['id'],99)['status'],'grouped')
        reviewed_group(99,self.selections([repository.draft(drafts[1]['id'],99)]),{'title':'Other','content_type':'movie'})

    def test_append_card_preserves_target_and_invalidates_package(self):
        drafts=[self.source('A'),self.source('B'),self.source('C')]
        target=reviewed_group(99,self.selections(drafts[:2]),{'title':'Keep','description':'Keep description','content_type':'movie'})
        repository.package_add(99,target['id'],repository.review_token(target))
        body={'target_id':target['id'],'target_token':repository.review_token(target)}
        with self.assertRaises(ValueError):reviewed_group(100,self.selections(drafts[2:]),body)
        updated=reviewed_group(99,self.selections(drafts[2:]),body)
        self.assertEqual(updated['id'],target['id'])
        self.assertEqual(updated['body']['description'],'Keep description')
        self.assertEqual(len(updated['body']['items']),3)
        self.assertEqual(repository.publication_package(99)['items'],[])
        with self.assertRaises(ValueError):reviewed_group(99,self.selections(drafts[2:]),body)

    def test_merge_two_grouped_cards(self):
        a=reviewed_group(99,self.selections([self.source('A')]),{'title':'A','content_type':'movie'})
        b=reviewed_group(99,self.selections([self.source('B')]),{'title':'B','content_type':'movie'})
        result=reviewed_group(99,self.selections([b]),{'target_id':a['id'],'target_token':repository.review_token(a)})
        self.assertEqual(len(result['body']['items']),2)
        self.assertEqual(repository.draft(b['id'],99)['status'],'grouped')
        result['body']['items'][1]['name']='Second variant'
        repository.save_draft(99,result['body'],a['id'])
        repository.publish(a['id'],99)

    def test_add_review_file_to_published_movie_preserves_original_file(self):
        number=add_new_movie('Published',movie_file='old-file',movie_file_type='video')
        current=self.source('New variant')
        body=current['body']
        body.update(movie_number=number,title='Published',content_type='movie')
        body['items'][0].update(category='Варианты',name='Новая озвучка')
        repository.save_draft(99,body,current['id'])
        self.assertEqual(repository.publish(current['id'],99),number)
        movie=database.execute('SELECT movie_file,movie_file_type FROM Movies WHERE movie_number=?',(number,)).fetchone()
        self.assertEqual(movie,('',''))
        rows=database.execute('''SELECT c.name,s.name,s.file_id FROM MovieCategories c
            JOIN MovieSubcategories s ON s.category_id=c.id WHERE c.movie_number=? ORDER BY s.id''',(number,)).fetchall()
        self.assertEqual(rows,[('Варианты','Основной файл','old-file'),('Варианты','Новая озвучка','ready')])
        self.assertEqual(repository.draft(current['id'],99)['status'],'published')

    def test_add_review_file_to_existing_category_and_reject_duplicate(self):
        from database.categories import add_movie_category,add_movie_subcategory
        number=add_new_movie('Published')
        category=add_movie_category(number,'1080p')
        add_movie_subcategory(category,'Studio A','old','video')
        current=self.source('Studio B')
        body=current['body'];body.update(movie_number=number,title='Published',content_type='movie')
        body['items'][0].update(category='1080P',name='Studio B')
        repository.save_draft(99,body,current['id']);repository.publish(current['id'],99)
        self.assertEqual(database.execute('SELECT count(*) FROM MovieCategories WHERE movie_number=?',(number,)).fetchone()[0],1)
        self.assertEqual(database.execute('SELECT count(*) FROM MovieSubcategories WHERE category_id=?',(category,)).fetchone()[0],2)
        duplicate=self.source('Duplicate');duplicate_body=duplicate['body']
        duplicate_body.update(movie_number=number,title='Published',content_type='movie')
        duplicate_body['items'][0].update(category='1080p',name='studio b')
        repository.save_draft(99,duplicate_body,duplicate['id'])
        with self.assertRaisesRegex(ValueError,'повторяться'):
            repository.publish(duplicate['id'],99)
        self.assertEqual(database.execute('SELECT count(*) FROM MovieSubcategories WHERE category_id=?',(category,)).fetchone()[0],2)

    def test_series_invitation_scopes_seasons_and_episodes(self):
        from database.categories import add_movie_category, add_movie_subcategory
        number=add_new_movie('Series', content_type='series')
        other=add_new_movie('Other', content_type='series')
        season=add_movie_category(number,'Season 1',1)
        outside=add_movie_category(other,'Season 1',1)
        episode=add_movie_subcategory(season,'Episode 1','file','video')
        token=movie_sharing.share_token(number)
        self.assertEqual(movie_sharing.accept(77,'share_'+token),number)
        for callback in [f'movcat_{season}',f'movsub_{episode}',f'wpage:{season}:1',f'wp:s:{season}:1:c:0',f'wp:e:{episode}:1:m:0',f'filecard_{number}']:
            self.assertTrue(movie_sharing.permits(77,callback),callback)
        for callback in [f'movcat_{outside}',f'wpage:{outside}:0','catalog:home',f'movcatback_{number}']:
            self.assertFalse(movie_sharing.permits(77,callback),callback)
