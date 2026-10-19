"""Group search presentation by exact artist name without claiming canonical identity.

This is a UI grouping of provider accounts, NOT a MusicBrainz identity
merge. Each provider-specific profile keeps its original source/id.
"""
import unicodedata


def artist_label(item):
    if not isinstance(item,dict) or item.get('kind') != 'artist':
        return ''
    title=item.get('title')
    if not isinstance(title,str):
        return ''
    cleaned=unicodedata.normalize('NFKC',' '.join(title.split())).casefold()
    # Short/generic names have a higher collision risk; keep distinct.
    return cleaned if len(cleaned)>=3 else ''


def provider_profile(item):
    """Keep only the fields needed to open the source's own artist card."""
    return {key: item[key] for key in ('source','id','kind','title','permalink','artwork')
            if key in item}


def group_artist_results(items):
    """One displayed search row per name, with explicit unverified alternatives.

    Never combine provider track IDs, musical releases or recordings.
    """
    output=[]
    groups={}
    seen=set()
    for item in items:
        if not isinstance(item,dict):
            continue
        identity=(item.get('source'),str(item.get('id')))
        if identity in seen:
            continue
        seen.add(identity)
        label=artist_label(item)
        if not label or not item.get('source') or not item.get('id'):
            output.append(item)
            continue
        if label not in groups:
            lead=dict(item)
            groups[label]=lead
            output.append(lead)
            continue
        lead=groups[label]
        if 'artist_profiles' not in lead:
            lead['artist_profiles']=[provider_profile(lead)]
            lead['identity_verified']=False
            lead['grouping']='name_only'
        lead['artist_profiles'].append(provider_profile(item))
        lead['artist_match_count']=len(lead['artist_profiles'])
    return output
