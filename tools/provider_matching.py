"""Disambiguate provider cards using trusted metadata, preserving the chosen card."""
import re
from tools.provider_identity import canonical_title, inline_title_matches, full_video_title_matches, source_name

COUNTRIES = {
 'RU': {'россия', 'russia'}, 'FR': {'франция', 'france'}, 'US': {'сша', 'usa', 'united states of america'},
 'GB': {'великобритания', 'uk', 'united kingdom'}, 'CA': {'канада', 'canada'},
 'DE': {'германия', 'germany'}, 'JP': {'япония', 'japan'}, 'CN': {'китай', 'china'},
 'IN': {'индия', 'india'}, 'KR': {'корея южная', 'южная корея', 'south korea'},
 'UA': {'украина', 'ukraine'}, 'IT': {'италия', 'italy'}, 'ES': {'испания', 'spain'},
 'AU': {'австралия', 'australia'}, 'PL': {'польша', 'poland'}, 'SU': {'ссср', 'soviet union'},
 'HK': {'гонконг', 'hong kong'}, 'TW': {'тайвань', 'taiwan'}, 'BR': {'бразилия', 'brazil'},
 'MX': {'мексика', 'mexico'}, 'ZA': {'юар', 'south africa'}, 'SE': {'швеция', 'sweden'},
 'DK': {'дания', 'denmark'}, 'FI': {'финляндия', 'finland'}, 'NO': {'норвегия', 'norway'},
 'NZ': {'новая зеландия', 'new zealand'}, 'CZ': {'чехия', 'czech republic'},
}


def country_codes(description):
    lines=(description or '').splitlines()
    if len(lines)<2:
        return set()
    values={canonical_title(v) for v in lines[1].split('|')[0].split(',')}
    return {code for code,names in COUNTRIES.items() if values & {canonical_title(n) for n in names}}


def source_key(row):
    sent=getattr(getattr(row,'send_message',None),'message',None)
    return sent if isinstance(sent,str) and re.fullmatch(r'kino_[0-9]{1,20}',sent) else None


def binding_for(row,query):
    return dict(title=row.title, query=query, source_key=source_key(row), countries=sorted(country_codes(getattr(row,'description',''))))


def valid_binding(value):
    return (isinstance(value,dict) and set(value)=={'title','query','source_key','countries'}
            and all(isinstance(value[k],str) and 1<=len(value[k])<=300 for k in ('title','query'))
            and (value['source_key'] is None or isinstance(value['source_key'],str) and re.fullmatch(r'kino_[0-9]{1,20}',value['source_key']))
            and isinstance(value['countries'],list) and len(value['countries'])<=20
            and all(isinstance(c,str) and re.fullmatch('[A-Z]{2}',c) for c in value['countries']))


def identity_score(row,payload):
    binding=payload.get('provider_binding')
    if binding:
        # A user-confirmed source identity takes precedence over catalogue aliases.
        if not valid_binding(binding) or row.title != binding['title']:
            return None
        if binding['source_key'] and source_key(row) != binding['source_key']:
            return None
        actual=country_codes(getattr(row,'description',''))
        if binding['countries'] and actual and set(binding['countries']) != actual:
            return None
        return 100
    identity=payload.get('identity') or {}
    original=identity.get('original_title') or ''
    names=[payload['title']] + ([original] if original else [])
    alias=re.search(r' \(([^()]*)\)?$',row.title or '')
    alias_exact=bool(original and alias and canonical_title(alias[1])==canonical_title(original))
    if not alias_exact and not any(inline_title_matches(row.title,n) for n in names):
        return None
    expected=set(identity.get('countries') or [])
    actual=country_codes(getattr(row,'description',''))
    if expected and actual and not expected & actual:
        return None
    score=4 if expected & actual else 0
    if original and alias:
        text=alias[1].rstrip(')… .')
        a,b=canonical_title(text),canonical_title(original)
        truncated=(row.title or '').rstrip(')').endswith(('…','...'))
        if a==b:
            score+=4
        elif truncated and len(a)>=6 and b.startswith(a):
            score+=2
        else:
            # An explicit different original name is evidence of a different work.
            return None
    elif original and canonical_title(original)==canonical_title(row.title):
        score+=2
    return score


def source_kind_year(row):
    header=(getattr(row,'description','') or '').split('\n')[0]
    tag=re.sub(r'^[^\w]+', '', header.split('|',1)[0].strip().casefold().replace('ё','е')).strip().replace('-',' ')
    kind=('series' if tag in {'сериал','мультсериал','аниме сериал','телесериал'} else
          'movie' if tag in {'фильм','мультфильм','аниме фильм','короткометражка'} else None)
    return kind, re.findall(r'\b(?:19|20)[0-9]{2}\b',header)


def clarification_candidate(row, query, payload):
    kind, years=source_kind_year(row)
    binding=binding_for(row,query)
    if (kind != payload['content_type'] or not years or not binding['source_key']
            or not valid_binding(binding)
            or payload.get('year') and str(payload['year']) not in years):
        return None
    description=(getattr(row,'description','') or '').splitlines()
    country=description[1].split('|')[0].strip()[:100] if len(description)>1 else ''
    label=f"{row.title} · {years[0]} · {'сериал' if kind=='series' else 'фильм'}"
    if country:
        label+=' · '+country
    return {'label':label[:400], 'binding':binding}


def media_title_matches(label,payload):
    names=[payload['title']]
    original=(payload.get('identity') or {}).get('original_title')
    if original:
        names.append(original)
    binding=payload.get('provider_binding')
    if binding and valid_binding(binding):
        name=source_name(binding['title'])
        if not name.endswith(('…','...')):
            names.append(name)
    return any(full_video_title_matches(label,n) for n in names)
