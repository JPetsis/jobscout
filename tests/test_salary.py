from jobscout import salary as salmod

CLIMATE = (
    "Remuneration : The range is EUR 4,363 (Masters plus 5+ relevant years outside of "
    "university studies) to EUR 4,810 (9+ years of relevant experience).")
REEF = ("Salary on a B2B contract:- 45-70 USD or 180-280 PLN per hour- Flexible work "
        "schedule (as measured with a time tracker)")


def test_eur_range_with_period_word():
    got = salmod.parse("", "Salary: €60,000 - €80,000 per year", "")
    assert got["currency"] == "EUR" and got["period"] == "year"
    assert got["min"] == 60000 and got["max"] == 80000
    assert got["annual_max"] == 80000


def test_two_currencies_hourly_range_picks_the_first_currency():
    got = salmod.parse("", REEF, "")
    assert got["currency"] == "USD" and got["period"] == "hour"
    assert got["min"] == 45 and got["max"] == 70
    assert got["annual_max"] == 70 * 2080


def test_compensation_field_wins_over_description():
    got = salmod.parse("", "we pay 30 USD per hour for support", "€65,000")
    assert got["currency"] == "EUR" and got["min"] == 65000 and got["max"] == 65000


def test_k_suffix_range_without_currency():
    got = salmod.parse("", "Compensation: 60-100k, depending on experience", "")
    assert got["min"] == 60000 and got["max"] == 100000 and got["currency"] == ""


def test_gbp_single_value():
    got = salmod.parse("", "Salary: £70k plus equity", "")
    assert got["currency"] == "GBP" and got["min"] == got["max"] == 70000
    assert got["period"] == "year"  # magnitude default, no period word needed


def test_usd_thousands_range():
    got = salmod.parse("", "Base pay $230,000 – $250,000 depending on level", "")
    assert got["currency"] == "USD" and got["min"] == 230000 and got["max"] == 250000


def test_noise_only_text_has_no_salary():
    assert salmod.parse("", "5+ years of experience, 40h/week, team of 30 people", "") is None
    assert salmod.parse("", "We have 30-40k customers and 100% remote work", "") is None
    assert salmod.parse("", "", "") is None


def test_monthly_range_inferred_from_hint_and_magnitude():
    got = salmod.parse("", CLIMATE, "")
    assert got["currency"] == "EUR" and got["period"] == "month"
    assert got["min"] == 4363 and got["max"] == 4810
    assert got["annual_max"] == 4810 * 12


def test_bare_year_numbers_are_not_salaries():
    got = salmod.parse("", "In 2026 we pay €80,000 for senior engineers", "")
    assert got["currency"] == "EUR" and got["min"] == 80000
    assert got["period"] == "year"


def test_fmt_chips():
    assert salmod.fmt(60000, 80000, "EUR", "year") == "€60k–80k/yr"
    assert salmod.fmt(45, 70, "USD", "hour") == "$45–70/hr"
    assert salmod.fmt(100000, None, "CHF", "year") == "CHF 100k/yr"
    assert salmod.fmt(5000, 5000, "EUR", "month") == "€5k/mo"
    assert salmod.fmt(None, None) == ""


def test_fmt_row_tolerates_missing_columns():
    assert salmod.fmt_row({"title": "x"}) == ""
    row = {"salary_min": 60000, "salary_max": 80000, "salary_currency": "EUR",
           "salary_period": "year"}
    assert salmod.fmt_row(row) == "€60k–80k/yr"

def test_annual_budget_is_not_a_salary():
    got = salmod.parse("", "A dedicated annual L&D budget of €2000 for your development", "")
    assert got is None
    ok = salmod.parse("", "Salary: £18,000 per year", "")
    assert ok and ok["min"] == 18000 and ok["period"] == "year"


def test_benefit_money_is_not_salary():
    """A real Gymondo advert, which publishes no salary at all.

    It says "Salary band: shared before your first interview" and then lists
    "€30 monthly mobility budget" and "€1,000 annual training budget". Grouping
    every EUR amount in that text produced a 30-1000 EUR monthly range, which is
    worse than no figure: it looks like a real band and it is a bike subsidy.
    """
    gymondo = (
        "Success requires positive energy.\n"
        "\U0001f3d6\ufe0f 30 days of vacation per year + Christmas Eve & New Year's Eve\n"
        "\U0001f6b2 \u20ac30 monthly mobility budget via Navit\n"
        "\U0001f4da \u20ac1,000 annual training budget + a wide range of opportunities "
        "for personal growth and development\n")
    assert salmod.parse("", gymondo, "") is None
    assert salmod.parse("", "\u20ac30 monthly mobility budget via Navit", "") is None
    assert salmod.parse("", "\u20ac1,000 annual training budget", "") is None

    # ...and the salary beside those benefits is still read, on both sides of it
    both = gymondo + "Salary: \u20ac60,000 - \u20ac80,000 per year"
    got = salmod.parse("", both, "")
    assert got and got["min"] == 60000 and got["max"] == 80000
    after = ("Salary: \u20ac60,000 - \u20ac80,000 per year. " + gymondo)
    got = salmod.parse("", after, "")
    assert got and got["min"] == 60000 and got["max"] == 80000

    # a pay word rescues an amount that merely mentions a budget
    named = salmod.parse("", "The salary budget for this role is \u20ac70,000 per year", "")
    assert named and named["max"] == 70000

    # ...and the plain words "benefit", "benefits" and "allowance" are NOT
    # budget words: an early version matched them within 60 characters and
    # cleared 753 real salaries ("£80k + benefits", "benefits of our package")
    for text, lo, hi in (
            ("Compensation: benefit from competitive salaries, starting from "
             "\u00a380k and above", 80000, 80000),
            ("\u00a380,000 - \u00a395,000 + benefits", 80000, 95000),
            ("Salary \u20ac60,000 per year plus a \u20ac1,000 training budget", 60000, 60000),
    ):
        got = salmod.parse("", text, "")
        assert got and got["min"] == lo and got["max"] == hi, (text, got)


def test_giant_ids_and_headcounts_are_not_salaries():
    noisy = ("You have 4 to 6 years of experience in Controlling. Collaborate with a "
             "diverse team of 3,000+ people. Ref 7246471902838317000 and "
             "newsitemid=20260316477473")
    assert salmod.parse("", noisy, "") is None
    ok = salmod.parse("", "Salary: €60,000 per year. " + noisy, "")
    assert ok and ok["max"] == 60000

def test_implausible_magnitudes_are_rejected():
    # 150k per hour is a parse mistake, not pay
    assert salmod.parse("", "Rate: $60 - $150k per hour for this contract", "") is None
    # 3,000+ people / 12,000 experts are headcounts, not salaries
    assert salmod.parse("", "Our 12,000 experts build software. Join the team!", "") is None


def test_retirement_plan_401k_is_not_a_salary():
    assert salmod.parse("", "We have a 401k match", "") is None
    got = salmod.parse("", "401(k) plan plus a salary of $120,000", "")
    assert got and got["max"] == 120000

def test_outlier_amounts_in_the_same_group_are_dropped():
    got = salmod.parse("", "Compensation: €500 allowance plus €90,000 salary per year", "")
    assert got["min"] == 90000 and got["max"] == 90000
    tiny = salmod.parse("", "We raised $1.6 and pay $275,000 per year", "")
    assert tiny["min"] == 275000


def test_space_separated_thousands():
    got = salmod.parse("", "Salary range: 15 600 - 27 000 PLN per month", "")
    assert got["min"] == 15600 and got["max"] == 27000 and got["currency"] == "PLN"
    assert got["period"] == "month"

def test_revenue_mentions_are_not_pay():
    revenue = ("Lead a portfolio of small merchants transacting between "
               "€5K and €50K in monthly volume")
    assert salmod.parse("", revenue, "") is None
    ok = salmod.parse("", "Monthly salary: €5,000", "")
    assert ok and ok["max"] == 5000 and ok["period"] == "month"
    pm = salmod.parse("", "B2B contract-based monthly remuneration is 38.000 - 45.000 PLN", "")
    assert pm and pm["min"] == 38000 and pm["max"] == 45000


def test_bare_numbers_need_a_strong_salary_word():
    weak = "Our rates are competitive. We have 12,000 users and 30,000 uploads"
    assert salmod.parse("", weak, "") is None
    strong = "Annual On-Target Earnings: 65k - 70k"
    assert salmod.parse("", strong, "")["min"] == 65000


def test_larajobs_style_compensation_field():
    # feed gives only a short compensation string, no description body
    got = salmod.parse("Full-Stack Developer (Laravel + Vue/Inertia)",
                       "Tags: API,Fullstack,Laravel,TailwindCSS,VueJS · Type: FULL_TIME",
                       "£60k")
    assert got["currency"] == "GBP" and got["min"] == got["max"] == 60000
    assert got["period"] == "year"


def test_global_currency_codes_and_scale_bounds():
    got = salmod.parse("", "", "85000-95000 MXN per month")
    assert got["currency"] == "MXN" and got["min"] == 85000 and got["period"] == "month"
    jpy = salmod.parse("", "", "6000000 JPY per year")
    assert jpy["currency"] == "JPY" and jpy["max"] == 6000000
    # the EUR-scale ceiling still bites for EUR-scale currencies
    assert salmod.parse("", "Salary: €900,000 per month", "") is None
