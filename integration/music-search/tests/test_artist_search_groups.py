import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from artist_search_groups import group_artist_results
from catalog_service import CatalogService


def artist(source, id_, name):
    return {'source':source,'id':str(id_),'kind':'artist',
            'title':name,'permalink':'https://example.org/'+source+'/'+str(id_)}


class PagingSource:
    def __init__(self, source, rows):
        self.source=source
        self.rows=rows
        self.calls=[]

    def search(self, query, page=1, kind='all'):
        self.calls.append((page,kind))
        off=(page-1)*10
        items=[dict(t) for t in self.rows[off:off+10]]
        return {'data':items,'has_more':off+10<len(self.rows)}


class ArtistSearchGroupingTests(unittest.TestCase):
    def test_same_label_is_one_row_with_all_original_provider_profiles(self):
        rows=[artist('soundcloud','one','Portishead'),
              artist('bandcamp','two','  PORTISHEAD  ')]
        copy_before=copy.deepcopy(rows)
        result=group_artist_results(rows)
        self.assertEqual(len(result),1)
        self.assertEqual(rows,copy_before)
        self.assertEqual(result[0]['kind'],'artist')
        self.assertEqual(result[0]['source'],'soundcloud')
        self.assertEqual(result[0]['id'],'one')
        self.assertEqual(result[0]['artist_match_count'],2)
        self.assertEqual(result[0]['grouping'],'name_only')
        self.assertFalse(result[0]['identity_verified'])
        self.assertEqual([(p['source'],p['id']) for p in result[0]['artist_profiles']],
                         [('soundcloud','one'),('bandcamp','two')])

    def test_same_name_does_not_claim_same_identity_or_drop_profiles(self):
        rows=[artist('soundcloud','original','Phoenix'),
              artist('bandcamp','other-person','Phoenix'),
              artist('soundcloud','cover-account','Phoenix')]
        result=group_artist_results(rows)
        self.assertEqual(len(result),1)
        self.assertEqual(len(result[0]['artist_profiles']),3)
        self.assertFalse(result[0]['identity_verified'])
        self.assertEqual([p['id'] for p in result[0]['artist_profiles']],
                         ['original','other-person','cover-account'])

    def test_different_names_very_short_names_and_recordings_stay_separate(self):
        rows=[artist('soundcloud','1','Portishead'),
              artist('bandcamp','2','Portishead Tribute'),
              artist('bandcamp','3','DJ'),artist('soundcloud','4','dj'),
              {'source':'bandcamp','id':'99','kind':'track','title':'Portishead','artist':'Other'},
              {'source':'soundcloud','id':'98','kind':'album','title':'Portishead'}]
        result=group_artist_results(rows)
        self.assertEqual(len(result),6)
        self.assertTrue(all('artist_profiles' not in t for t in result))

    def test_search_groups_before_pagination_and_no_repeated_artist(self):
        sc=[artist('soundcloud',i,'Portishead' if i in (0,11,22)
                   else 'SC Artist '+str(i)) for i in range(26)]
        bc=[artist('bandcamp',i,'Portishead' if i in (0,10,19)
                   else 'BC Artist '+str(i)) for i in range(24)]
        catalog=CatalogService(PagingSource('soundcloud',sc),
                               PagingSource('bandcamp',bc),
                               PagingSource('audius',[]))
        one=catalog.search('Portishead',1,'artist')
        two=catalog.search('Portishead',2,'artist')
        three=catalog.search('Portishead',3,'artist')
        all_rows=one['data']+two['data']+three['data']
        self.assertEqual([t['title'] for t in all_rows].count('Portishead'),1)
        self.assertEqual(len(all_rows),30)
        self.assertEqual(len({t['title'] for t in all_rows}),30)
        self.assertTrue(one['has_more'])
        self.assertTrue(two['has_more'])
        portishead=one['data'][0]
        self.assertFalse(portishead['identity_verified'])
        self.assertEqual(len(portishead['artist_profiles']),6)

    def test_mixed_search_groups_only_artists_not_tracks(self):
        sc=[artist('soundcloud','1','Portishead'),
            {'source':'soundcloud','id':'2','kind':'track','title':'Roads','artist':'Portishead'}]
        bc=[artist('bandcamp','x','Portishead'),
            {'source':'bandcamp','id':'y','kind':'track','title':'Roads','artist':'Portishead'}]
        catalog=CatalogService(PagingSource('soundcloud',sc),
                               PagingSource('bandcamp',bc),
                               PagingSource('audius',[]))
        result=catalog.search('Portishead',1,'all')['data']
        self.assertEqual(len(result),3)
        self.assertEqual([x['kind'] for x in result],['artist','track','track'])
        self.assertEqual(result[0]['artist_match_count'],2)


if __name__=='__main__':
    unittest.main()
