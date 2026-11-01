"""The language vote: what it must decide, and what it must refuse to decide."""
from jobscout.language import allowed, detect

SV = ("Vi söker en vikarierande antikvarie med ansvar för animalosteologi. "
      "Du kommer att arbeta med våra osteologiska samlingar under 2027 och "
      "du ska ha mycket god kännedom om svenska arkeologiska förhållanden.")
NO = ("Førsteamanuensis innen nordisk arkeologi - vikingtid og middelalder. "
      "Stillingen er ved universitetet og søker skal ha doktorgrad innen faget.")
DE = ("W 1-Professur (mit Tenure Track nach W 2) für Ur- und Frühgeschichte. "
      "Die Stelle ist unbefristet und die Bewerbung ist bis zum 30. November "
      "möglich. Die Universität bietet eine Vergütung nach W 2.")
EN = ("Lecturer in Archaeology and Heritage. The department is seeking a "
      "candidate with a PhD and teaching experience, and the position is "
      "available from January. Applications are welcome until 30 November.")
FR = ("Maître de conférences en archéologie préhistorique. Le poste est ouvert "
      "aux candidats titulaires d'un doctorat et l'enseignement se fait en "
      "français, dans le cadre du département.")
IT = ("Ricercatore in archeologia preistorica. Il posto è presso il "
      "dipartimento e la candidatura deve essere presentata entro il termine "
      "previsto dal bando, con un dottorato di ricerca.")
NL = ("Hoogleraar archeologie. Het een en ander is te vinden op de website van "
      "de universiteit en de sollicitatie kan tot 1 december worden gestuurd.")


def test_it_names_the_language_of_a_real_advert():
    assert detect(SV) == "sv"
    assert detect(NO) == "no"
    assert detect(DE) == "de"
    assert detect(EN) == "en"
    assert detect(FR) == "fr"
    assert detect(IT) == "it"
    assert detect(NL) == "nl"


def test_a_short_title_is_not_an_answer():
    """Four words are not evidence, and a guess here hides a real job."""
    assert detect("Archaeologist (m/w/d)") == ""
    assert detect("Archäologe/Archäologin aller Fachrichtungen") == ""
    assert detect("") == ""


def test_a_close_vote_is_not_an_answer():
    # Danish and Norwegian share most stopwords; the vote must not pretend.
    assert detect("og det som en et på er av for med til den har", margin=5) == ""


def test_the_allow_list_only_removes_what_it_can_prove():
    # Empty list: the filter is off, everything passes.
    assert allowed("sv", []) is True
    # Unknown language passes: nothing was proven.
    assert allowed("", ["en", "de"]) is True
    # A named language that is not allowed is the one case that drops a row.
    assert allowed("sv", ["en", "de"]) is False
    # Case and whitespace in the config are forgiven.
    assert allowed("de", [" EN ", "De"]) is True
