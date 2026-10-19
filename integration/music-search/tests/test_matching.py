from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from track_matching import assess, rank_matches


def track(**changes):
    return {'id':'1','source':'soundcloud','kind':'track','title':'Song','artist':'Artist','duration':180,**changes}


class MatchingTests(unittest.TestCase):
    def test_exact_identity_and_close_duration_auto_select(self):
        result=rank_matches(track(),[track(duration=181)])
        self.assertEqual(result['state'],'matched')
        self.assertEqual(result['selected']['id'],'1')

    def test_preview_wrong_artist_version_and_unknown_duration_never_auto_select(self):
        for changes in [{'preview':True},{'artist':'Cover Artist'},{'duration':0},{'duration':240},
                        {'title':'Song (Live)'},{'title':'Song (Remix)'},{'title':'Song (Remastered)'},
                        {'title':'Song (Кавер)'},{'title':'Song Instrumental'}]:
            with self.subTest(changes=changes):
                self.assertIsNone(rank_matches(track(),[track(**changes)])['selected'])

    def test_duplicates_of_same_recording_across_services_do_not_create_false_ambiguity(self):
        result=rank_matches(track(),[track(source=source) for source in ['soundcloud','bandcamp','audius']])
        self.assertEqual(result['state'],'matched')
        self.assertIsNotNone(result['selected'])
        self.assertEqual(len(result['data']),3)

    def test_equal_distance_conflicting_durations_need_review(self):
        result=rank_matches(track(duration=200),[track(duration=197),track(id='2',duration=203)])
        self.assertIsNone(result['selected'])
        self.assertEqual(result['state'],'review')

    def test_normalization_and_artist_prefix_are_supported(self):
        original=track(title='Love to the People',artist='Sue Barker')
        candidate=track(title='Sue Barker - Love to the People',artist='Uploader')
        self.assertTrue(assess(original,candidate)['automatic'])
        self.assertTrue(assess(track(artist='Beyoncé'),track(artist='Beyonce'))['automatic'])

    def test_featured_artist_credit_is_compared_not_discarded(self):
        original=track(title='Kasaneru (feat. iri)',artist='Pigeondust, iri')
        candidate=track(title='Kasaneru feat. iri',artist='Pigeondust')
        self.assertTrue(assess(original,candidate)['automatic'])
        self.assertFalse(assess(original,track(title='Kasaneru',artist='Pigeondust'))['automatic'])

    def test_known_isrc_mismatch_and_unavailable_track_are_rejected(self):
        self.assertFalse(assess(track(isrc='USAAA1234567'),track(isrc='USBBB1234567'))['automatic'])
        self.assertIsNone(rank_matches(track(),[track(unavailable=True)])['selected'])
