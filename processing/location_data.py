"""
Telangana district gazetteer used by processing/location.py.

Only Telangana is covered — these aliases were built for the original
Telangana political pipeline. Add other states here (same shape: canonical
district -> list of lowercase aliases in every script it appears in) to
extend district detection beyond Telangana.
"""

LOCATION_KEYWORDS = {
    'Hyderabad':      ['hyderabad', 'హైదరాబాద్', 'ghmc', 'charminar', 'old city'],
    'Secunderabad':   ['secunderabad', 'సికింద్రాబాద్', 'cantonment'],
    'Warangal':       ['warangal', 'వరంగల్', 'hanamkonda', 'హనుమకొండ'],
    'Karimnagar':     ['karimnagar', 'కరీంనగర్'],
    'Nizamabad':      ['nizamabad', 'నిజామాబాద్'],
    'Khammam':        ['khammam', 'ఖమ్మం'],
    'Nalgonda':       ['nalgonda', 'నల్గొండ'],
    'Mahabubnagar':   ['mahabubnagar', 'mahbubnagar', 'మహబూబ్‌నగర్', 'palamuru'],
    'Adilabad':       ['adilabad', 'ఆదిలాబాద్'],
    'Siddipet':       ['siddipet', 'సిద్దిపేట'],
    'Sangareddy':     ['sangareddy', 'సంగారెడ్డి'],
    'Rangareddy':     ['rangareddy', 'ranga reddy', 'రంగారెడ్డి'],
    'Medak':          ['medak', 'మెదక్'],
    'Kodangal':       ['kodangal', 'కొడంగల్'],
    'Madhira':        ['madhira', 'మధిర'],
    'Gajwel':         ['gajwel', 'గజ్వేల్'],
    'Sircilla':       ['sircilla', 'సిరిసిల్ల', 'rajanna sircilla'],
    'Suryapet':       ['suryapet', 'సూర్యాపేట'],
    'Nagarkurnool':   ['nagarkurnool', 'నాగర్‌కర్నూల్'],
    'Vikarabad':      ['vikarabad', 'వికారాబాద్'],
    'Bhadrachalam':   ['bhadrachalam', 'భద్రాచలం', 'bhadradri'],
    'Yadadri':        ['yadadri', 'యాదాద్రి', 'bhuvanagiri', 'bhongir'],
}

# ── Telangana centroid Lat/Lng ────────────────────────────────────────────────
TELANGANA_LAT = 17.9784
TELANGANA_LNG = 79.5941
