"""Synthetic bulk-v1 vocabulary and fixed industry choices; not demographic data."""

FIRST = (
    "Aaron",
    "Abigail",
    "Adam",
    "Adela",
    "Adrian",
    "Ahmed",
    "Aisha",
    "Akira",
    "Alba",
    "Alejandra",
    "Alex",
    "Alexandra",
    "Alicia",
    "Alina",
    "Amelia",
    "Amir",
    "Ananya",
    "Andre",
    "Andres",
    "Anika",
    "Anton",
    "Arjun",
    "Asma",
    "Audrey",
    "Ayana",
    "Bao",
    "Ben",
    "Benjamin",
    "Bilal",
    "Bo",
    "Bridget",
    "Bruno",
    "Carlos",
    "Carmen",
    "Catherine",
    "Celeste",
    "Charlotte",
    "Chen",
    "Chris",
    "Clara",
    "Colin",
    "Connor",
    "Daniel",
    "Daniela",
    "David",
    "Deepa",
    "Dimitri",
    "Dina",
    "Dylan",
    "Edith",
    "Elena",
    "Eli",
    "Elise",
    "Elizabeth",
    "Emilia",
    "Emma",
    "Eric",
    "Esther",
    "Evelyn",
    "Farah",
    "Felix",
    "Fernando",
    "Flora",
    "Francesca",
    "Freya",
    "Gabriel",
    "Gemma",
    "George",
    "Grace",
    "Greta",
    "Hannah",
    "Harini",
    "Hassan",
    "Hazel",
    "Helena",
    "Hiro",
    "Ibrahim",
    "Ida",
    "Imran",
    "Ines",
    "Iris",
    "Isaac",
    "Isabella",
    "Ivan",
    "Jacob",
    "Jade",
    "James",
    "Jana",
    "Javier",
    "Jay",
    "Jean",
    "Jenna",
    "Jessica",
    "Jia",
    "Joel",
    "Johan",
    "Jonas",
    "Jordan",
    "Jose",
    "Joseph",
    "Julia",
    "Julian",
    "Kai",
    "Kamal",
    "Karina",
    "Karim",
    "Kavya",
    "Keiko",
    "Kevin",
    "Kiara",
    "Kiran",
    "Klara",
    "Lara",
    "Laura",
    "Leah",
    "Leila",
    "Leo",
    "Leon",
    "Liam",
    "Lina",
    "Lisa",
    "Livia",
    "Louis",
    "Lucia",
    "Luka",
    "Luna",
    "Maeve",
    "Mahmoud",
    "Malik",
    "Manon",
    "Marco",
    "Maria",
    "Marina",
    "Mark",
    "Martin",
    "Mary",
    "Maya",
    "Mei",
    "Mila",
    "Mina",
    "Miriam",
    "Mohammed",
    "Monica",
    "Nadia",
    "Natalia",
    "Nathan",
    "Neha",
    "Nia",
    "Nico",
    "Nina",
    "Noor",
    "Nora",
    "Olivia",
    "Omar",
    "Pablo",
    "Paolo",
    "Paul",
    "Pedro",
    "Priya",
    "Rafael",
    "Raul",
    "Reem",
    "Ricardo",
    "Rita",
    "Rosa",
    "Rowan",
    "Rumi",
    "Ruth",
    "Sakura",
    "Salma",
    "Samuel",
    "Sana",
    "Sarah",
    "Sasha",
    "Selena",
    "Selin",
    "Shreya",
    "Silvia",
    "Sofia",
    "Sonia",
    "Sora",
    "Stefan",
    "Sunil",
    "Surya",
    "Tara",
    "Tariq",
    "Theo",
    "Thomas",
    "Tobias",
    "Tomas",
    "Vera",
    "Victor",
    "Vivian",
    "Wei",
    "Ximena",
    "Yara",
    "Youssef",
    "Yuki",
    "Zayn",
    "Zeynep",
)

LAST = (
    "Adams",
    "Ahmed",
    "Alvarez",
    "Andersen",
    "Arora",
    "Adebayo",
    "Agrawal",
    "Akhtar",
    "Amari",
    "Anand",
    "Andrade",
    "Armstrong",
    "Aziz",
    "Bae",
    "Baker",
    "Balogun",
    "Barrett",
    "Bauer",
    "Bell",
    "Bennett",
    "Bhatia",
    "Bianchi",
    "Blake",
    "Bose",
    "Braun",
    "Brooks",
    "Byrne",
    "Cabral",
    "Campos",
    "Cao",
    "Carlson",
    "Carpenter",
    "Carter",
    "Castillo",
    "Chandra",
    "Chao",
    "Chen",
    "Cheung",
    "Cho",
    "Chowdhury",
    "Clarke",
    "Cole",
    "Collins",
    "Cruz",
    "Dahl",
    "Davies",
    "Davis",
    "Demir",
    "Desai",
    "Doshi",
    "Dubois",
    "Duncan",
    "Edwards",
    "Eriksen",
    "Evans",
    "Farouk",
    "Fernandes",
    "Ferreira",
    "Fischer",
    "Ford",
    "Foster",
    "Fu",
    "Fujita",
    "Garg",
    "Ghosh",
    "Gomez",
    "Gonzalez",
    "Grant",
    "Gupta",
    "Haddad",
    "Hamada",
    "Hansen",
    "Hassan",
    "Hayashi",
    "Henderson",
    "Hernandez",
    "Hoffman",
    "Hong",
    "Hu",
    "Huang",
    "Ibrahim",
    "Inoue",
    "Ishikawa",
    "Ivanov",
    "Jain",
    "James",
    "Jiang",
    "Johansson",
    "Jones",
    "Joshi",
    "Khan",
    "Kim",
    "Koch",
    "Koh",
    "Kumar",
    "Kwon",
    "Larsen",
    "Laurent",
    "Le",
    "Lee",
    "Lewis",
    "Liang",
    "Lim",
    "Liu",
    "Lopez",
    "Luo",
    "Lynch",
    "Mahmoud",
    "Malik",
    "Martinez",
    "Mason",
    "Matsumoto",
    "Mehta",
    "Mendoza",
    "Meyer",
    "Mitchell",
    "Mohamed",
    "Morales",
    "Morgan",
    "Mukherjee",
    "Muller",
    "Nakamura",
    "Nakano",
    "Nelson",
    "Nguyen",
    "Nishimura",
    "Novak",
    "Oliveira",
    "Olsen",
    "Osei",
    "Ozturk",
    "Parker",
    "Patel",
    "Pereira",
    "Peters",
    "Pham",
    "Pinto",
    "Popov",
    "Powell",
    "Prasad",
    "Rahman",
    "Rai",
    "Ramos",
    "Rao",
    "Reid",
    "Reyes",
    "Richardson",
    "Rivera",
    "Robinson",
    "Rodrigues",
    "Romano",
    "Romero",
    "Roy",
    "Ruiz",
    "Sakai",
    "Sakamoto",
    "Santos",
    "Sato",
    "Schneider",
    "Scott",
    "Sharma",
    "Shen",
    "Shin",
    "Silva",
    "Smith",
    "Solberg",
    "Sousa",
    "Srinivasan",
    "Stewart",
    "Su",
    "Sullivan",
    "Svensson",
    "Takahashi",
    "Tanaka",
    "Tang",
    "Teixeira",
    "Thomas",
    "Torres",
    "Tran",
    "Ueda",
    "Ullah",
    "Verma",
    "Vieira",
    "Walker",
    "Wang",
    "Watanabe",
    "Weber",
    "White",
    "Williams",
    "Wong",
    "Wood",
    "Xu",
    "Yadav",
    "Yamada",
    "Yang",
    "Yoshida",
    "Yu",
    "Zhao",
    "Zheng",
    "Zhu",
)

FAMILIES = (
    "Engineering",
    "Product",
    "Sales",
    "Marketing",
    "Customer Support",
    "Finance",
    "People",
    "Legal",
    "IT",
    "Operations",
)
ROLES = (
    "Software Engineer",
    "Product Manager",
    "Account Executive",
    "Marketing Specialist",
    "Customer Support Specialist",
    "Financial Analyst",
    "People Specialist",
    "Legal Counsel",
    "Systems Engineer",
    "Operations Analyst",
)
# Entry-level bands; seniority multipliers below are synthetic USD lab policy.
BASE_BANDS = (
    (75000, 95000),
    (70000, 90000),
    (60000, 80000),
    (55000, 70000),
    (45000, 56000),
    (60000, 76000),
    (53000, 68000),
    (75000, 95000),
    (65000, 82000),
    (48000, 64000),
)
IC_LEVELS = ("Associate", "II", "Senior", "Staff", "Principal")
LEVEL_FACTORS = (1, 1.25, 1.55, 1.85, 2.15)
INDUSTRIES = {
    "northstar": {
        "industry": "software",
        "locations": ("Seattle", "Austin", "Boston", "Dublin", "Singapore"),
        "weights": (35, 12, 15, 7, 10, 4, 4, 2, 5, 6),
        "departments": (
            ("Platform Engineering", "Developer Experience", "Application Engineering"),
            ("Core Product", "Product Research", "Product Design"),
            ("Enterprise Sales", "EMEA Sales", "Partner Sales"),
            ("Product Marketing", "Growth Marketing", "Brand Communications"),
            ("Customer Success", "Technical Support", "Customer Education"),
            ("Financial Planning", "Revenue Accounting", "Procurement"),
            ("People Experience", "Talent Acquisition", "Total Rewards"),
            ("Commercial Legal", "Privacy Counsel", "Corporate Governance"),
            ("Business Systems", "Corporate Security", "Workplace Technology"),
            ("Business Operations", "Revenue Operations", "Workplace Services"),
        ),
        "projects": (
            "usage billing",
            "developer onboarding",
            "service reliability",
            "partner launch",
            "accessibility",
            "release readiness",
        ),
    },
    "meridian": {
        "industry": "healthcare",
        "locations": ("Chicago", "Minneapolis", "Atlanta", "Phoenix", "Baltimore"),
        "weights": (18, 8, 9, 5, 17, 8, 7, 5, 8, 15),
        "departments": (
            (
                "Clinical Systems Engineering",
                "Interoperability Engineering",
                "Data Platform",
            ),
            (
                "Care Navigation Products",
                "Patient Experience",
                "Clinical Product Design",
            ),
            ("Provider Partnerships", "Health Plan Sales", "Regional Accounts"),
            ("Provider Marketing", "Community Outreach", "Health Communications"),
            ("Member Services", "Provider Support", "Care Navigation Support"),
            ("Clinical Finance", "Reimbursement Analysis", "Accounts Payable"),
            ("Workforce Planning", "Clinical Recruiting", "Employee Relations"),
            ("Healthcare Legal", "Privacy Operations", "Contract Counsel"),
            ("Clinical Applications", "Service Desk", "Information Security"),
            ("Care Operations", "Scheduling Operations", "Quality Operations"),
        ),
        "projects": (
            "appointment access",
            "provider enrollment",
            "care handoffs",
            "service quality",
            "clinic scheduling",
            "member communications",
        ),
    },
    "cedar": {
        "industry": "retail",
        "locations": ("Dallas", "Columbus", "Denver", "Portland", "Charlotte"),
        "weights": (12, 6, 9, 8, 15, 7, 8, 3, 7, 25),
        "departments": (
            ("Commerce Engineering", "Fulfillment Systems", "Retail Data Platform"),
            ("Shopping Experience", "Loyalty Products", "Merchandising Products"),
            ("Wholesale Sales", "Regional Sales", "Marketplace Partnerships"),
            ("Seasonal Campaigns", "Customer Insights", "Retail Brand"),
            ("Order Support", "Customer Care", "Returns Support"),
            ("Retail Finance", "Inventory Accounting", "Commercial Planning"),
            ("Store Talent", "Learning and Development", "People Services"),
            ("Retail Compliance", "Employment Counsel", "Supplier Contracts"),
            ("Store Technology", "Infrastructure Services", "Business Applications"),
            ("Store Operations", "Supply Chain Operations", "Payroll Operations"),
        ),
        "projects": (
            "seasonal assortment",
            "stock availability",
            "store opening",
            "delivery accuracy",
            "loyalty enrollment",
            "returns experience",
        ),
    },
}
TEAM_NAMES = (
    "Enablement",
    "Planning",
    "Delivery",
    "Analytics",
    "Quality",
    "Systems",
    "Services",
    "Partnerships",
)


def architecture(slug, count, org_count, position_count, rng):
    flavor = INDUSTRIES[slug]
    family_count = min(10, org_count - 1)
    orgs = [
        {
            "name": f"{slug.title()} Executive Office",
            "parent": None,
            "family": 0,
            "depth": 0,
        }
    ]
    for family in range(family_count):
        orgs.append(
            {"name": FAMILIES[family], "parent": 0, "family": family, "depth": 1}
        )
    for family in range(family_count):
        if len(orgs) == org_count:
            break
        orgs.append(
            {
                "name": flavor["departments"][family][0],
                "parent": family + 1,
                "family": family,
                "depth": 2,
            }
        )
    while len(orgs) < org_count:
        family = rng.choices(
            range(family_count), weights=flavor["weights"][:family_count]
        )[0]
        departments = [
            i for i, o in enumerate(orgs) if o["family"] == family and o["depth"] == 2
        ]
        if len(departments) < 3 and (len(orgs) % 3 == 0 or not departments):
            parent = family + 1
            name = flavor["departments"][family][len(departments)]
        else:
            parent = departments[0] if not departments else rng.choice(departments)
            pods = [
                i
                for i, o in enumerate(orgs)
                if o["parent"] == parent and o["depth"] == 3
            ]
            if len(pods) >= 4:
                parent = pods[len(orgs) % len(pods)]
            siblings = sum(o["parent"] == parent for o in orgs)
            name = f"{orgs[parent]['name']} {TEAM_NAMES[siblings % len(TEAM_NAMES)]}"
            if siblings >= len(TEAM_NAMES):
                name += " " + flavor["locations"][siblings // len(TEAM_NAMES) - 1]
        orgs.append(
            {
                "name": name,
                "parent": parent,
                "family": family,
                "depth": orgs[parent]["depth"] + 1,
            }
        )

    names, used = [], set()
    for _ in range(count):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        name = f"{first} {last}"
        for middle in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            if name not in used:
                break
            name = f"{first} {middle}. {last}"
        while name in used:
            first, last = rng.choice(FIRST), rng.choice(LAST)
            name = f"{first} {last}"
        used.add(name)
        names.append(name)

    vacancies = {i for i in range(1, org_count) if i % 17 == 0}
    positions, salaries, levels = [], [], []
    for i in range(count):
        family = (
            orgs[i]["family"]
            if i < org_count
            else rng.choices(
                range(family_count), weights=flavor["weights"][:family_count]
            )[0]
        )
        if i in (org_count, org_count + 1) and family_count == 10:
            family = 6
        choices = [
            n for n, o in enumerate(orgs) if o["family"] == family and o["depth"] >= 2
        ]
        org = rng.choice(choices) if choices else family + 1
        level = rng.choices(range(5), weights=(25, 38, 25, 9, 3))[0]
        manager = i < org_count and i not in vacancies
        if manager:
            org = orgs[i]["parent"] or 0
            depth = orgs[i]["depth"]
            rank = (
                "VP"
                if depth == 1
                else "Director"
                if depth == 2
                else "Senior Manager"
                if any(o["parent"] == i for o in orgs)
                else "Manager"
            )
            title = (
                "Chief Executive Officer" if i == 0 else f"{rank}, {orgs[i]['name']}"
            )
            low, high = (
                (240000, 280000)
                if i == 0
                else (200000, 260000)
                if depth == 1
                else (155000, 205000)
                if depth == 2
                else (115000, 165000)
            )
        else:
            base = ROLES[family]
            title = f"{base} II" if level == 1 else f"{IC_LEVELS[level]} {base}"
            if i == org_count and family_count == 10:
                title = "Senior HR Business Partner"
                level = 2
            elif i == org_count + 1 and family_count == 10:
                title = "Senior Compensation Analyst"
                level = 2
            low, high = (int(n * LEVEL_FACTORS[level]) for n in BASE_BANDS[family])
        salary = max(48000, rng.randrange((low + 999) // 1000, high // 1000 + 1) * 1000)
        positions.append(
            {
                "title": title,
                "org": org,
                "family": family,
                "level": level,
                "manager": manager,
            }
        )
        levels.append(level)
        salaries.append(
            [int(salary * 0.94) // 100 * 100, int(salary * 0.97) // 100 * 100, salary]
        )
    historical = [[i, i, i] for i in range(count)]
    for org in sorted(vacancies):
        positions.append(
            {
                "title": f"Manager, {orgs[org]['name']}",
                "org": orgs[org]["parent"] or 0,
                "family": orgs[org]["family"],
                "level": 0,
                "manager": True,
                "vacancy_org": org,
            }
        )
    for i, position in enumerate(positions[:count]):
        if (
            not position["manager"]
            and levels[i] > 0
            and i % 10 == 0
            and len(positions) < position_count - 1
        ):
            lower = levels[i] - 1
            role = ROLES[position["family"]]
            title = f"{role} II" if lower == 1 else f"{IC_LEVELS[lower]} {role}"
            historical[i][:2] = [len(positions)] * 2
            positions.append(position | {"title": title, "level": lower})
            low, high = BASE_BANDS[position["family"]]
            prior = (
                min(int(high * LEVEL_FACTORS[lower]), int(salaries[i][2] / 1.10))
                // 100
                * 100
            )
            salaries[i][:2] = [int(prior * 0.97) // 100 * 100, prior]
    while len(positions) < position_count:
        source = positions[org_count + (len(positions) % (count - org_count))]
        positions.append(source.copy())
    return names, orgs, positions, historical, salaries


SECTIONS = {
    "Offer Letter": (
        "Your appointment",
        "Pay and review cycle",
        "Working arrangements",
        "Learning allowance",
        "First ninety days",
        "Benefits enrollment",
        "Confidential information",
        "Manager check-ins",
    ),
    "Performance Note": (
        "Delivery outcomes",
        "Partner feedback",
        "Judgment and ownership",
        "Technical growth",
        "Customer impact",
        "Collaboration",
        "Development priorities",
        "Next review commitments",
    ),
    "Onboarding Note": (
        "First-week introductions",
        "Access requests",
        "Role shadowing",
        "Team rituals",
        "Learning pathway",
        "Support contacts",
        "Safety orientation",
        "Thirty-day checkpoint",
    ),
    "Policy Acknowledgement": (
        "Scope and ownership",
        "Employee responsibilities",
        "Approval paths",
        "Exceptions",
        "Records and retention",
        "Escalation contacts",
        "Annual review",
        "Practical examples",
    ),
}


def document_text(
    slug, index, title, name, position, org_name, salaries, injection, size, rng
):
    flavor = INDUSTRIES[slug]
    location = flavor["locations"][(index // 4) % len(flavor["locations"])]
    kind = (
        "Offer Letter",
        "Performance Note",
        "Onboarding Note",
        "Policy Acknowledgement",
    )[index % 4]
    intro = f"SYNTHETIC LAB DOCUMENT\n{slug.title()} | {flavor['industry'].title()} | {title}\nEmployee: {name}\nLocation: {location}\nOrganization: {org_name}\nRole: {position['title']}\n"
    if kind == "Offer Letter":
        intro += f"Original 2024 base salary: USD {salaries[0]:,}. The 2025 review set USD {salaries[1]:,}; the 2026 review set USD {salaries[2]:,}. Figures are annual base pay, excluding benefits and incentives.\n"
    intro += injection + "\n"
    sections = list(SECTIONS[kind])
    rng.shuffle(sections)
    paragraphs = []
    for number in range(40):
        project = rng.choice(flavor["projects"])
        partner = rng.choice(FAMILIES)
        month = rng.choice(("January", "March", "May", "July", "September", "November"))
        cadence = rng.choice(("weekly", "fortnightly", "monthly", "quarterly"))
        measure = rng.choice(
            (
                "handoff clarity",
                "response time",
                "rework",
                "completion quality",
                "customer feedback",
                "training coverage",
            )
        )
        action = rng.choice(
            (
                "review the agreed checklist",
                "record decisions and owners",
                "compare outcomes with the prior period",
                "document open questions",
                "walk through a representative case",
                "confirm the escalation path",
            )
        )
        finding = rng.choice(
            (
                "The last review identified a coordination gap at the handoff.",
                "Feedback highlighted clear documentation and timely follow-through.",
                "The team agreed to simplify the intake form before the next review.",
                "Capacity planning remains the main dependency for this period.",
                "A small pilot will validate the revised process before wider adoption.",
                "The review found that ownership was clear but response times varied.",
            )
        )
        paragraphs.append(
            f"\n{sections[number % len(sections)]} — {month} working note\nFor {project}, {org_name} works with {partner} colleagues in {location}. {finding} {name} should {action} with a {cadence} check-in. Track {measure}, agree evidence with the manager and retain only synthetic examples in this lab. The {flavor['industry']} context informs the examples; this document does not establish an exception to access controls.\n"
        )
    body = (intro + "".join(paragraphs)).encode("utf-8")
    # Exact byte targets remain stable even for names or headings containing Unicode.
    content = body[:size].decode("utf-8", errors="ignore")
    return content + " " * (size - len(content.encode("utf-8")))
