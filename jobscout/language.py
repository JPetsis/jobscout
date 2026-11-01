"""Which language an advert is written in, from the words it uses.

One decision depends on this: is the advert in a language the user accepts?
Nothing else does, so the detector is small, dependency-free and deliberately
cowardly.

It votes with stopwords — the words a language cannot avoid — and answers ""
when the text is too short or the vote is close. An unknown advert is KEPT: the
expensive mistake is hiding a job because a guess went the wrong way, not
showing one extra card.

Why stopwords and not a library: the profiles are read on a laptop with no
network, the ads are short, and a 3 MB model to tell German from Dutch is not
worth the install. Measured on the store, the failure this must avoid is
"German advert read as Dutch", so the sets below share no distinctive word
between de and nl.
"""
from __future__ import annotations

import re

#: Languages the detector can name. Anything else reads as "" (unknown).
LANGUAGES = ("en", "de", "fr", "it", "es", "nl", "sv", "no", "da", "pt")

# Distinctive words only: a token shared by two languages is not evidence for
# either, and "de" (French/Spanish/Dutch/Portuguese) would vote four ways.
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset("""
        the and of to with is are was were will would should can could you your
        we our this that these those be been being as on from have has had by
        or an who whom whose their they them there not also more than about
        position role department university research teaching application
        applicant candidates offer salary contract full-time part-time
    """.split()),
    "de": frozenset("""
        der die das und mit für von im den dem des ist sind war wird werden wir
        sie sich auf als bei aus oder ein eine einen einem einer eines haben
        hat nicht auch zur zum sowie ihre ihrer unsere über nach unter kann
        müssen soll sollen nachrichtlich bewerbung bewerberinnen stellenangebot
        unbefristet befristet vollzeit teilzeit vergütung
    """.split()),
    "fr": frozenset("""
        le la les et en un une du pour dans sur au aux avec est sont par que qui
        vous nous ce cette être plus leur leurs ses son sa mais ou donc car
        poste candidature candidat recrutement université recherche enseignement
    """.split()),
    "it": frozenset("""
        il lo gli le di che per con del della dei delle al alla nel nella su
        è sono da come non più anche tra fra questo questa loro nostro nostra
        posto candidatura candidati università ricerca insegnamento
    """.split()),
    "es": frozenset("""
        el los las y que por con del para se es son al como más su sus pero
        este esta estos estas muy sin sobre entre puesto candidatura candidato
        universidad investigación docencia
    """.split()),
    "nl": frozenset("""
        het een van op te dat die zijn voor met aan er niet ook als bij om dan
        wordt kunnen wij jij je onze hun deze dit maar nog wel geen vacature
        sollicitatie sollicitant universiteit onderzoek onderwijs
    """.split()),
    "sv": frozenset("""
        och att det som en ett på är av för med till den har inte du vi om ska
        kan söker tjänst vikarierande samt eller från vid också blir kommer
        universitet forskning undervisning ansökan
    """.split()),
    "no": frozenset("""
        og å det som en et på er av for med til den har ikke du vi om skal kan
        innen søker stilling ved også blir kommer universitet forskning
        undervisning søknad
    """.split()),
    "da": frozenset("""
        og at det som en et på er af for med til den har ikke du vi om skal kan
        søger stilling ved også bliver kommer universitet forskning
        undervisning ansøgning
    """.split()),
    "pt": frozenset("""
        o os as e que por com do da dos das para se é são ao como mais seu sua
        mas este esta muito sem sobre entre vaga candidatura candidato
        universidade investigação ensino
    """.split()),
}

_WORD = re.compile(r"[a-zà-öø-ÿœæ]+", re.I)


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def detect(text: str, min_hits: int = 2, margin: int = 1) -> str:
    """The language of this text, or "" when the vote does not settle it.

    `min_hits` is the number of stopwords a language needs before it is called,
    and `margin` how far it must lead the runner-up. Both exist so a title of
    four words ("Archaeologist (m/w/d)") answers "unknown" instead of guessing.
    """
    words = _words(text)
    if len(words) < 4:
        return ""
    votes: dict[str, int] = {}
    for lang, stops in _STOPWORDS.items():
        hits = sum(1 for w in words if w in stops)
        if hits:
            votes[lang] = hits
    if not votes:
        return ""
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    best, hits = ranked[0]
    if hits < min_hits:
        return ""
    if len(ranked) > 1 and hits - ranked[1][1] < margin:
        return ""            # a close vote is not an answer
    return best


def allowed(lang: str, allow) -> bool:
    """Whether a detected language passes the allow list.

    Unknown ("") always passes, and an empty list means the filter is off. Both
    rules are the same idea: this filter only removes what it can prove.
    """
    if not allow:
        return True
    if not lang:
        return True
    return lang in {str(a).strip().lower() for a in allow if str(a).strip()}
