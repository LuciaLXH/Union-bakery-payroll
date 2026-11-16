"""Short-lived per-session display reads; never used for write validation."""
from copy import deepcopy
from hashlib import sha256
import os
from time import monotonic

import streamlit as st


def clear_reads():
    st.session_state.pop('_display_reads', None)


def read(key, loader, *, schema, ttl=30):
    from ui.access import check_access
    check_access()  # Authorization must run even on a cache hit.
    scope = (os.environ.get('APP_MODE'), schema,
             sha256(os.environ.get('DATABASE_URL', '').encode()).hexdigest())
    cache = st.session_state.setdefault('_display_reads', {})
    now = monotonic()
    # Bound storage and discard entries belonging to another environment.
    for old in list(cache):
        if old[0] != scope or now >= cache[old][0]:
            del cache[old]
    cache_key = (scope, key)
    if cache_key not in cache:
        cache[cache_key] = (now + ttl, deepcopy(loader()))
    return deepcopy(cache[cache_key][1])
