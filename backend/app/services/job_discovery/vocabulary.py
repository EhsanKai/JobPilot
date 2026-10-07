"""Deterministic vocabularies used by the job normalizer.

Plain keyword tables, no AI. Each table is ordered: the first match wins.
They are intentionally conservative -- an unknown value stays unknown
rather than being guessed -- and are expected to grow over time.
"""

import re

from app.models.job import SalaryPeriod
from app.models.job_preferences import EmploymentTypeValue, RemotePreference
from app.models.professional_profile import SeniorityLevel

# --- Employment type -----------------------------------------------------------

# Matched against a source's explicit employment-type text (e.g. Lever's
# "commitment"). English + German.
EMPLOYMENT_TYPE_PATTERNS: tuple[tuple[EmploymentTypeValue, re.Pattern[str]], ...] = (
    (EmploymentTypeValue.WORKING_STUDENT, re.compile(r"working.student|werkstudent", re.I)),
    (EmploymentTypeValue.INTERNSHIP, re.compile(r"\bintern(ship)?s?\b|praktik", re.I)),
    (EmploymentTypeValue.FREELANCE, re.compile(r"freelanc|freiberuf", re.I)),
    (EmploymentTypeValue.CONTRACT, re.compile(r"\bcontract(or|ing)?\b", re.I)),
    (
        EmploymentTypeValue.TEMPORARY,
        re.compile(r"temporar|\btemp\b|fixed.term|befristet|seasonal|aushilfe", re.I),
    ),
    (EmploymentTypeValue.PART_TIME, re.compile(r"part.?time|teilzeit", re.I)),
    (
        EmploymentTypeValue.FULL_TIME,
        re.compile(r"full.?time|vollzeit|permanent|unbefristet|\bregular\b", re.I),
    ),
)

# Only unambiguous signals are inferred from a job *title*. ("Contract
# Manager" is not a contract job, so "contract" is not one of them.)
EMPLOYMENT_TYPE_TITLE_PATTERNS: tuple[tuple[EmploymentTypeValue, re.Pattern[str]], ...] = (
    (EmploymentTypeValue.WORKING_STUDENT, re.compile(r"working.student|werkstudent", re.I)),
    (EmploymentTypeValue.INTERNSHIP, re.compile(r"\bintern(ship)?\b|praktikant|praktikum", re.I)),
    (EmploymentTypeValue.FREELANCE, re.compile(r"\bfreelance", re.I)),
    (EmploymentTypeValue.PART_TIME, re.compile(r"part.?time|teilzeit", re.I)),
)

# --- Work model --------------------------------------------------------------------

WORK_MODEL_PATTERNS: tuple[tuple[RemotePreference, re.Pattern[str]], ...] = (
    (RemotePreference.HYBRID, re.compile(r"hybrid", re.I)),
    (
        RemotePreference.REMOTE,
        re.compile(r"remote|home.?office|work from home|wfh|mobiles arbeiten", re.I),
    ),
    (
        RemotePreference.ONSITE,
        re.compile(r"on.?site|in.?office|office.based|in.person|vor ort|präsenz", re.I),
    ),
    (RemotePreference.FLEXIBLE, re.compile(r"flexib", re.I)),
)

# Inferred from location/title text. "On-site" is never inferred: a location
# without the word "remote" doesn't mean the job is on-site.
WORK_MODEL_LOCATION_PATTERNS: tuple[tuple[RemotePreference, re.Pattern[str]], ...] = (
    (RemotePreference.HYBRID, re.compile(r"\bhybrid\b", re.I)),
    (
        RemotePreference.REMOTE,
        # "Remote Sensing Engineer" is not a remote job.
        re.compile(r"\bremote\b(?!\s+(sensing|control|monitoring))|\bhome.?office\b", re.I),
    ),
)

# --- Seniority (from title) ------------------------------------------------------

SENIORITY_TITLE_PATTERNS: tuple[tuple[SeniorityLevel, re.Pattern[str]], ...] = (
    (
        SeniorityLevel.INTERN,
        re.compile(r"\bintern(ship)?\b|praktikant|praktikum|werkstudent|working.student", re.I),
    ),
    (
        SeniorityLevel.DIRECTOR,
        re.compile(r"\bdirector\b|\bhead of\b|\bvp\b|vice president|\bchief\b|\bc[tefio]o\b", re.I),
    ),
    (
        SeniorityLevel.LEAD,
        re.compile(
            r"\blead\b(?!\s+gen)|\bprincipal\b|\bstaff\b|teamleit|team.?lead|tech.?lead", re.I
        ),
    ),
    (SeniorityLevel.SENIOR, re.compile(r"\bsenior\b|\bsr\.?(?=\s)", re.I)),
    (
        SeniorityLevel.JUNIOR,
        re.compile(r"\bjunior\b|\bjr\.?(?=\s)|entry.level|\bgraduate\b|berufseinsteiger", re.I),
    ),
    (SeniorityLevel.MID, re.compile(r"\bmid.?level\b|\bmedior\b|\bintermediate\b", re.I)),
)

# --- Salary --------------------------------------------------------------------------

SALARY_PERIOD_PATTERNS: tuple[tuple[SalaryPeriod, re.Pattern[str]], ...] = (
    (SalaryPeriod.HOUR, re.compile(r"hour|stunde|/\s*h\b", re.I)),
    (SalaryPeriod.DAY, re.compile(r"\bday|daily|\btag", re.I)),
    (SalaryPeriod.WEEK, re.compile(r"week|woche", re.I)),
    (SalaryPeriod.MONTH, re.compile(r"month|monat", re.I)),
    (SalaryPeriod.YEAR, re.compile(r"year|annual|annum|\bp\.?\s?a\.?\b|jahr|jährlich", re.I)),
)

CURRENCY_SYMBOLS: dict[str, str] = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY", "₹": "INR"}
CURRENCY_CODES = frozenset(
    {"EUR", "USD", "GBP", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK", "PLN", "CZK",
     "HUF", "JPY", "INR", "SGD", "HKD", "BRL", "MXN", "ZAR", "ILS", "TRY", "AED", "RON"}
)

# --- Technologies ------------------------------------------------------------------

# (canonical name, pattern, case_sensitive). Case-sensitive entries are words
# that also occur in ordinary English ("react", "spark", "swift", "rust").
_TECH: tuple[tuple[str, str, bool], ...] = (
    ("Python", r"python", False),
    ("Java", r"java", False),
    ("JavaScript", r"javascript", False),
    ("TypeScript", r"typescript", False),
    ("Kotlin", r"kotlin", False),
    ("Scala", r"scala", False),
    ("Go", r"golang", False),
    ("Rust", r"Rust", True),
    ("C++", r"c\+\+", False),
    ("C#", r"c#", False),
    (".NET", r"\.net", False),
    ("Ruby", r"Ruby", True),
    ("Ruby on Rails", r"ruby on rails|Rails", True),
    ("PHP", r"php", False),
    ("Swift", r"Swift", True),
    ("Objective-C", r"objective-c", False),
    ("Elixir", r"elixir", False),
    ("Haskell", r"haskell", False),
    ("Clojure", r"clojure", False),
    ("SQL", r"sql", False),
    ("PostgreSQL", r"postgres(ql)?", False),
    ("MySQL", r"mysql", False),
    ("MongoDB", r"mongo(db)?", False),
    ("Redis", r"redis", False),
    ("Elasticsearch", r"elastic\s?search", False),
    ("Kafka", r"kafka", False),
    ("RabbitMQ", r"rabbitmq", False),
    ("Snowflake", r"Snowflake", True),
    ("BigQuery", r"bigquery", False),
    ("dbt", r"dbt", False),
    ("Apache Spark", r"Spark|pyspark", True),
    ("Airflow", r"airflow", False),
    ("Hadoop", r"hadoop", False),
    ("Docker", r"docker", False),
    ("Kubernetes", r"kubernetes|k8s", False),
    ("Terraform", r"terraform", False),
    ("Ansible", r"ansible", False),
    ("Jenkins", r"jenkins", False),
    ("GitHub Actions", r"github actions", False),
    ("CI/CD", r"ci\s?/\s?cd", False),
    ("AWS", r"aws|amazon web services", False),
    ("GCP", r"gcp|google cloud", False),
    ("Azure", r"azure", False),
    ("Linux", r"linux", False),
    ("React", r"React(\.js|js)?", True),
    ("React Native", r"React Native", True),
    ("Angular", r"angular(js)?", False),
    ("Vue.js", r"vue(\.js|js)?", False),
    ("Next.js", r"next\.js|nextjs", False),
    ("Node.js", r"node\.js|nodejs", False),
    ("Django", r"django", False),
    ("Flask", r"Flask", True),
    ("FastAPI", r"fastapi", False),
    ("Spring Boot", r"spring boot", False),
    ("GraphQL", r"graphql", False),
    ("gRPC", r"grpc", False),
    ("Flutter", r"flutter", False),
    ("iOS", r"ios", False),
    ("Android", r"android", False),
    ("TensorFlow", r"tensorflow", False),
    ("PyTorch", r"pytorch", False),
    ("pandas", r"pandas", False),
    ("scikit-learn", r"scikit-learn|sklearn", False),
    ("Tableau", r"tableau", False),
    ("Power BI", r"power\s?bi", False),
    ("Looker", r"Looker", True),
    ("Figma", r"figma", False),
    ("Salesforce", r"salesforce", False),
    ("SAP", r"SAP", True),
)

TECHNOLOGY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        name,
        re.compile(
            rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9+#])", 0 if case_sensitive else re.I
        ),
    )
    for name, pattern, case_sensitive in _TECH
)
