"""Skill taxonomy (V0.5.1): canonical skills, aliases, categories.

Domain-neutral: a food-safety standard, a spreadsheet tool, an accounting
framework and a programming language are all ordinary entries; nothing in
the code depends on any of them. Categories are data -- add one by adding
a `Category`.

The seed is deliberately small. Every entry was observed in the 503 real
Greenhouse/Lever postings captured in V0.4.1 (enforced by
`tests/test_taxonomy_real_data.py`; counts and reasoning in
`docs/taxonomy.md`). Aliases are explicit spelling variants of the *same*
thing -- observed in that data, already in the V0.4 technology vocabulary,
or plainly equivalent (e.g. "JS"). Related-but-different things are never
aliases: "Java" is not "JavaScript", "Lean Six Sigma" is not "Six Sigma",
"Google Cloud" is not every Google Cloud service.

Ambiguity is data too: an alias that also means something else (`Alias`
with `ambiguity`) and vendor names covering many products (`AmbiguousTerm`)
yield AMBIGUOUS instead of a guess.
"""

from app.services.taxonomy.model import (
    Alias,
    AmbiguousTerm,
    Category,
    Entry,
    NormalizationResult,
    Vocabulary,
)
from app.services.taxonomy.versions import TAXONOMY_VERSION, VOCABULARY_VERSIONS

CATEGORIES = tuple(
    Category(key, label)
    for key, label in (
        ("programming_language", "Programming language"),
        ("framework", "Framework"),
        ("library", "Library"),
        ("runtime", "Runtime environment"),
        ("database", "Database"),
        ("data_engineering", "Data engineering"),
        ("cloud", "Cloud platform"),
        ("devops", "DevOps"),
        ("mobile", "Mobile platform"),
        ("data_science", "Data science & AI"),
        ("business_intelligence", "Business intelligence"),
        ("business_software", "Business software"),
        ("enterprise_software", "Enterprise software"),
        ("design", "Design"),
        ("marketing", "Marketing"),
        ("finance", "Finance & accounting"),
        ("engineering", "Engineering"),
        ("quality_management", "Quality management"),
        ("food_safety", "Food safety"),
        ("occupational_safety", "Occupational safety"),
        ("supply_chain", "Supply chain"),
        ("logistics", "Logistics"),
        ("healthcare", "Healthcare"),
        ("project_management", "Project management"),
        ("methodology", "Methodology"),
        ("certification", "Certification"),
        ("other", "Other"),
    )
)


def _entries() -> tuple[Entry, ...]:
    e = Entry
    return (
        # --- Programming languages ----------------------------------------------------
        e("python", "Python", "programming_language"),
        e("sql", "SQL", "programming_language"),
        e("java", "Java", "programming_language"),
        e("javascript", "JavaScript", "programming_language", ("js", "java script")),
        e("typescript", "TypeScript", "programming_language"),
        e("kotlin", "Kotlin", "programming_language"),
        e("scala", "Scala", "programming_language"),
        e(
            "go",
            "Go",
            "programming_language",
            ("golang", Alias("go", "also an ordinary English word")),
        ),
        e(
            "swift",
            "Swift",
            "programming_language",
            (Alias("swift", "also SWIFT, the interbank messaging network (finance)"),),
        ),
        e(
            "r",
            "R",
            "programming_language",
            ("r language", "r programming", Alias("r", "single letter (e.g. in 'R&D')")),
        ),
        # --- Frameworks, libraries, runtimes ----------------------------------------
        e("react", "React", "library", ("react.js", "reactjs")),
        e("react_native", "React Native", "framework"),
        e("vue_js", "Vue.js", "framework", ("vue", "vuejs")),
        e("spring_boot", "Spring Boot", "framework"),
        e(
            "node_js",
            "Node.js",
            "runtime",
            ("nodejs", Alias("node", "also a generic term (network or graph node)")),
        ),
        e("pandas", "pandas", "library"),
        e("scikit_learn", "scikit-learn", "library", ("sklearn",)),
        # --- Databases and data engineering -----------------------------------------
        e("postgresql", "PostgreSQL", "database", ("postgres",)),
        e("mysql", "MySQL", "database"),
        e("snowflake", "Snowflake", "database"),
        e("bigquery", "BigQuery", "database", ("google bigquery",)),
        e("apache_kafka", "Apache Kafka", "data_engineering", ("kafka",)),
        e("apache_airflow", "Apache Airflow", "data_engineering", ("airflow",)),
        e("apache_spark", "Apache Spark", "data_engineering", ("spark",)),
        e("dbt", "dbt", "data_engineering", ("data build tool",)),
        # --- Cloud, DevOps, mobile ----------------------------------------------------
        e("aws", "Amazon Web Services", "cloud", ("aws",)),
        e(
            "google_cloud",
            "Google Cloud",
            "cloud",
            (
                "google cloud platform",
                Alias("gcp", "also Good Clinical Practice (clinical research)"),
            ),
        ),
        e("microsoft_azure", "Microsoft Azure", "cloud", ("azure",)),
        e("docker", "Docker", "devops"),
        e("kubernetes", "Kubernetes", "devops", ("k8s",)),
        e("ci_cd", "CI/CD", "devops"),
        e("terraform", "Terraform", "devops"),
        e("git", "Git", "devops"),
        e("ios", "iOS", "mobile"),
        e("android", "Android", "mobile"),
        # --- Data science & AI ----------------------------------------------------------
        e(
            "machine_learning",
            "Machine learning",
            "data_science",
            (Alias("ml", "also millilitre"),),
        ),
        e(
            "large_language_models",
            "Large language models",
            "data_science",
            ("llm", "llms", "large language model"),
        ),
        # --- Business intelligence and business software ------------------------------
        e("tableau", "Tableau", "business_intelligence"),
        e("looker", "Looker", "business_intelligence"),
        e("power_bi", "Power BI", "business_intelligence", ("powerbi", "microsoft power bi")),
        e("microsoft_excel", "Microsoft Excel", "business_software", ("excel", "ms excel")),
        e("microsoft_office", "Microsoft Office", "business_software", ("ms office",)),
        e(
            "microsoft_powerpoint",
            "Microsoft PowerPoint",
            "business_software",
            ("powerpoint", "ms powerpoint"),
        ),
        e("google_sheets", "Google Sheets", "business_software"),
        e("google_workspace", "Google Workspace", "business_software", ("g suite", "gsuite")),
        e("jira", "Jira", "business_software"),
        e("confluence", "Confluence", "business_software"),
        e("salesforce", "Salesforce", "business_software"),
        e("hubspot", "HubSpot", "business_software"),
        e("zendesk", "Zendesk", "business_software"),
        e("workday", "Workday", "business_software"),
        e("crm", "CRM", "business_software", ("customer relationship management",)),
        # --- Enterprise software (system classes, not vendors) ------------------------
        e("erp", "ERP", "enterprise_software", ("enterprise resource planning",)),
        e(
            "warehouse_management_systems",
            "Warehouse management systems",
            "enterprise_software",
            (
                "wms",
                "warehouse management system",
                # German plural, observed ("Warehouse-Management-Systemen").
                "warehouse management systemen",
            ),
        ),
        # --- Design and marketing ------------------------------------------------------
        e("figma", "Figma", "design"),
        e(
            "seo",
            "Search engine optimization",
            "marketing",
            ("seo", "search engine optimisation"),
        ),
        e("google_analytics", "Google Analytics", "marketing"),
        e("google_ads", "Google Ads", "marketing", ("adwords", "google adwords")),
        e("a_b_testing", "A/B testing", "methodology", ("a/b tests", "a/b test")),
        # --- Finance -----------------------------------------------------------------------
        e("accounting", "Accounting", "finance"),
        e("budgeting", "Budgeting", "finance"),
        e("financial_modeling", "Financial modeling", "finance", ("financial modelling",)),
        e("ifrs", "IFRS", "finance", ("international financial reporting standards",)),
        e(
            "fp_and_a",
            "Financial planning & analysis",
            "finance",
            ("fp&a", "financial planning and analysis"),
        ),
        # --- Certifications -------------------------------------------------------------
        e(
            "certified_public_accountant",
            "Certified Public Accountant",
            "certification",
            (Alias("cpa", "also 'cost per acquisition' (marketing metric)"),),
        ),
        e(
            "acca",
            "ACCA",
            "certification",
            ("association of chartered certified accountants",),
        ),
        e(
            "apics_cpim",
            "APICS CPIM",
            "certification",
            ("cpim", "certified in production and inventory management"),
        ),
        # --- Quality, food safety, occupational safety --------------------------------
        e(
            "gmp",
            "Good Manufacturing Practice",
            "quality_management",
            ("gmp", "good manufacturing practices"),
        ),
        e("haccp", "HACCP", "food_safety", ("hazard analysis and critical control points",)),
        e("sqf", "SQF", "food_safety", ("safe quality food",)),
        e("osha", "OSHA", "occupational_safety"),
        # --- Engineering ---------------------------------------------------------------
        e(
            "plc",
            "Programmable logic controllers",
            "engineering",
            (
                Alias("plc", "also 'public limited company'"),
                "programmable logic controller",
                "plc programming",
                "plc logic",
                "plc controls",
            ),
        ),
        # --- Methodologies and project management --------------------------------------
        e("agile", "Agile", "methodology"),
        e("scrum", "Scrum", "methodology"),
        e(
            "lean_manufacturing",
            "Lean manufacturing",
            "methodology",
            (
                "lean methodology",
                "lean methodologies",
                "lean principles",
                Alias("lean", "also an ordinary English word; Lean Startup is different"),
            ),
        ),
        e("six_sigma", "Six Sigma", "methodology"),
        e("lean_six_sigma", "Lean Six Sigma", "methodology"),
        e("project_management", "Project management", "project_management"),
        e("stakeholder_management", "Stakeholder management", "project_management"),
        # --- Supply chain and logistics --------------------------------------------------
        e(
            "supply_chain_management",
            "Supply chain management",
            "supply_chain",
            (
                "supply chain",
                Alias("scm", "also source control / software configuration management"),
            ),
        ),
        e("procurement", "Procurement", "supply_chain"),
        e("inventory_management", "Inventory management", "supply_chain"),
        e("logistics", "Logistics", "logistics"),
        e(
            "forklift_operation",
            "Forklift operation",
            "logistics",
            ("forklift", "forklifts"),
        ),
    )


AMBIGUOUS_TERMS = (
    AmbiguousTerm(
        "SAP",
        "vendor name covering many distinct products (e.g. SAP IBP, SAP S/4HANA); "
        "name the product",
    ),
    AmbiguousTerm("Oracle", "vendor name: a database, an ERP suite and other products"),
    AmbiguousTerm("CAD", "currency code (Canadian dollar) or computer-aided design"),
)

SKILLS = Vocabulary(
    name="skills",
    version=VOCABULARY_VERSIONS["skills"],
    taxonomy_version=TAXONOMY_VERSION,
    categories=CATEGORIES,
    entries=_entries(),
    ambiguous_terms=AMBIGUOUS_TERMS,
)


def normalize_skill(text: str | None) -> NormalizationResult | None:
    """Normalize one skill term (not free text). None for missing input."""
    if text is None or not text.strip():
        return None
    return SKILLS.lookup(text)
