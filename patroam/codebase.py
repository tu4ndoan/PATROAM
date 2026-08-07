"""Codebase understanding — what a project is built with and how it's laid out.

Reads a project folder and reports its languages, tech stack, directory
structure and key files. Deterministic (no LLM): manifests and file extensions
are exact, so this is fast, offline and always correct.

Deliberately CURATED, not exhaustive: hanabie alone has 53 npm dependencies, and
putting every one on the knowledge graph would bury it. `stack()` keeps only
technologies worth reasoning about (frameworks, databases, major services), so
projects sharing a stack visibly connect on the graph.
"""

import json
import os
import re

# Directories never worth walking: dependencies, build output, VCS, engine cruft.
SKIP_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".next", ".nuxt", "dist",
    "build", "out", ".venv", "venv", "env", ".idea", ".vscode", "coverage",
    ".pytest_cache", ".mypy_cache", ".turbo", ".cache", "vendor", "target",
    # Unreal Engine generated folders — enormous and meaningless here
    "Binaries", "Intermediate", "DerivedDataCache", "Saved", "Build",
}

# Source extension → language.
LANGS = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust",
    ".java": "Java", ".kt": "Kotlin", ".swift": "Swift", ".rb": "Ruby",
    ".php": "PHP", ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".h": "C++", ".hpp": "C++", ".c": "C", ".m": "Objective-C",
    ".dart": "Dart", ".lua": "Lua", ".sh": "Shell", ".sql": "SQL",
    ".css": "CSS", ".scss": "CSS", ".html": "HTML", ".vue": "Vue",
    ".svelte": "Svelte",
}

# Dependency name → display name on the graph. Only things that describe the
# ARCHITECTURE of a project; helper libs (clsx, tailwind-merge, @types/*) are out.
_TECH = {
    # web frameworks / runtime
    "next": "Next.js", "react": "React", "react-dom": "React", "vue": "Vue",
    "svelte": "Svelte", "@angular/core": "Angular", "express": "Express",
    "fastify": "Fastify", "nestjs": "NestJS", "@nestjs/core": "NestJS",
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django",
    "uvicorn": "Uvicorn", "electron": "Electron", "vite": "Vite",
    # data
    "prisma": "Prisma", "@prisma/client": "Prisma", "mongoose": "MongoDB",
    "pg": "Postgres", "psycopg2": "Postgres", "psycopg2-binary": "Postgres",
    "mysql2": "MySQL", "sqlalchemy": "SQLAlchemy", "redis": "Redis",
    "chromadb": "ChromaDB", "sqlite3": "SQLite", "drizzle-orm": "Drizzle",
    "@supabase/supabase-js": "Supabase",
    # auth / payments / infra
    "next-auth": "NextAuth", "@auth/prisma-adapter": "NextAuth",
    "stripe": "Stripe", "@stripe/stripe-js": "Stripe",
    "firebase": "Firebase", "@aws-sdk/client-s3": "AWS S3",
    "@upstash/ratelimit": "Upstash Redis", "resend": "Resend",
    "bcryptjs": "bcrypt", "jsonwebtoken": "JWT",
    # ui / styling
    "tailwindcss": "Tailwind CSS", "framer-motion": "Framer Motion",
    "@mui/material": "Material UI", "bootstrap": "Bootstrap",
    "lucide-react": "lucide-react", "three": "Three.js",
    # state / data fetching
    "zustand": "Zustand", "redux": "Redux", "@reduxjs/toolkit": "Redux",
    "@tanstack/react-query": "React Query", "swr": "SWR",
    # validation / forms
    "zod": "Zod", "react-hook-form": "React Hook Form",
    # testing
    "vitest": "Vitest", "jest": "Jest", "@playwright/test": "Playwright",
    "playwright": "Playwright", "pytest": "pytest", "cypress": "Cypress",
    # AI / ML / media
    "anthropic": "Claude API", "openai": "OpenAI API", "ollama": "Ollama",
    "langchain": "LangChain", "torch": "PyTorch", "tensorflow": "TensorFlow",
    "opencv-python": "OpenCV", "cv2": "OpenCV", "numpy": "NumPy",
    "pandas": "pandas", "scikit-learn": "scikit-learn", "pillow": "Pillow",
    "mcp": "MCP", "transformers": "Transformers",
    # desktop / voice
    "pywebview": "pywebview", "kivy": "Kivy", "pyqt5": "Qt", "pyside6": "Qt",
    "pyttsx3": "pyttsx3", "edge-tts": "edge-tts", "pygame": "pygame",
    "speechrecognition": "SpeechRecognition", "pyaudio": "PyAudio",
    "faster-whisper": "Whisper", "openai-whisper": "Whisper",
    # docs / reporting
    "reportlab": "ReportLab", "pypdf": "pypdf", "pymupdf": "PyMuPDF",
}

# Marker file/dir → technology (things with no dependency entry).
_MARKERS = [
    ("docker-compose.yml", "Docker"), ("docker-compose.yaml", "Docker"),
    ("Dockerfile", "Docker"), ("next.config.ts", "Next.js"),
    ("next.config.js", "Next.js"), ("next.config.mjs", "Next.js"),
    ("tailwind.config.ts", "Tailwind CSS"), ("tailwind.config.js", "Tailwind CSS"),
    ("vercel.json", "Vercel"), ("netlify.toml", "Netlify"),
    ("pubspec.yaml", "Flutter"), ("Cargo.toml", "Rust"), ("go.mod", "Go"),
    ("pom.xml", "Maven"), ("build.gradle", "Gradle"),
    (".github", "GitHub Actions"), ("prisma", "Prisma"),
    ("terraform", "Terraform"), ("k8s", "Kubernetes"),
]

# Files that explain a project at a glance, in priority order.
_KEY_FILES = [
    "README.md", "ARCHITECTURE.md", "package.json", "requirements.txt",
    "pyproject.toml", "app.py", "main.py", "index.js", "server.js",
    "next.config.ts", "next.config.js", "docker-compose.yml", "Dockerfile",
    "prisma/schema.prisma", "middleware.ts", "tsconfig.json", "go.mod",
    "Cargo.toml", "pubspec.yaml", "CLAUDE.md", "TODO.md", "PLAN.md",
]


def _read(path, limit=200_000):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read(limit)
    except Exception:
        return ""


def _norm_dep(name):
    return (name or "").strip().lower()


def _deps(folder):
    """Every declared dependency name across the manifests we understand."""
    out = []
    pkg = os.path.join(folder, "package.json")
    if os.path.isfile(pkg):
        try:
            data = json.loads(_read(pkg)) or {}
            for key in ("dependencies", "devDependencies"):
                out += list((data.get(key) or {}).keys())
        except Exception:
            pass
    req = os.path.join(folder, "requirements.txt")
    if os.path.isfile(req):
        for line in _read(req).splitlines():
            line = line.split("#")[0].strip()
            if line:
                # strip version/extras: "uvicorn[standard]>=0.2" → "uvicorn"
                out.append(re.split(r"[\[<>=!;\s]", line, 1)[0])
    toml = os.path.join(folder, "pyproject.toml")
    if os.path.isfile(toml):
        for m in re.finditer(r'^\s*"?([A-Za-z0-9._-]+)"?\s*[>=<~^]', _read(toml), re.M):
            out.append(m.group(1))
    return [d for d in (x.strip() for x in out) if d]


def languages(folder, max_files=4000):
    """{language: file count}, biggest first — from source file extensions."""
    counts = {}
    seen = 0
    for dp, dns, fns in os.walk(folder):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in fns:
            lang = LANGS.get(os.path.splitext(fn)[1].lower())
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
                seen += 1
        if seen > max_files:
            break
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


# Ranked so the cap keeps what DEFINES the project. Anything not listed here is
# incidental (icon packs, helpers, small utilities) and is dropped — the project
# view should read like an architecture summary, not a dependency dump.
_TIER = {t: i for i, group in enumerate([
    # 0 — the framework / engine the project IS
    ("Next.js", "React", "Vue", "Svelte", "Angular", "Django", "Flask", "FastAPI",
     "Express", "NestJS", "Electron", "Unreal Engine", "Flutter", ".NET",
     "Kivy", "Qt", "pywebview"),
    # 1 — how it stores data
    ("Prisma", "Postgres", "MySQL", "MongoDB", "SQLite", "Redis", "Supabase",
     "SQLAlchemy", "Drizzle", "ChromaDB", "Firebase"),
    # 2 — the services / capabilities it depends on
    ("Stripe", "NextAuth", "AWS S3", "Resend", "Upstash Redis", "Docker",
     "Claude API", "OpenAI API", "Ollama", "MCP", "Whisper", "OpenCV",
     "PyTorch", "TensorFlow", "Vercel", "JWT"),
    # 3 — notable architectural libraries
    ("Tailwind CSS", "Zustand", "Redux", "React Query", "Zod", "Three.js",
     "SpeechRecognition", "Playwright", "Vitest", "Jest", "pytest"),
]) for t in group}


def stack(folder, limit=8):
    """The technologies that DEFINE this project — ranked, deduped, capped.

    Only tiered technologies are returned: a project view should say
    "Next.js · Prisma · Stripe", not list 53 npm packages."""
    found = []

    def add(t):
        if t and t not in found:
            found.append(t)

    for d in _deps(folder):
        t = _TECH.get(_norm_dep(d))
        if t:
            add(t)
    for marker, tech in _MARKERS:
        if os.path.exists(os.path.join(folder, marker)):
            add(tech)
    # Engines / ecosystems that announce themselves with a project file
    try:
        for fn in os.listdir(folder):
            low = fn.lower()
            if low.endswith(".uproject"):
                add("Unreal Engine")
            elif low.endswith(".sln") or low.endswith(".csproj"):
                add(".NET")
            elif low.endswith(".xcodeproj"):
                add("Xcode")
    except Exception:
        pass
    # Keep only what's ranked — untiered entries are incidental libraries.
    # (Capture discovery order up front: sort() reorders `found` as it runs.)
    order = {t: i for i, t in enumerate(found)}
    found = [t for t in found if t in _TIER]
    found.sort(key=lambda t: (_TIER[t], order[t]))
    return found[:limit]


def structure(folder, limit=12):
    """Top-level source directories, with how many files each holds."""
    rows = []
    try:
        for d in sorted(os.listdir(folder)):
            p = os.path.join(folder, d)
            if not os.path.isdir(p) or d in SKIP_DIRS or d.startswith("."):
                continue
            n = 0
            for dp, dns, fns in os.walk(p):
                dns[:] = [x for x in dns if x not in SKIP_DIRS and not x.startswith(".")]
                n += len(fns)
                if n > 5000:
                    break
            rows.append({"name": d, "files": n})
    except Exception:
        return []
    rows.sort(key=lambda r: r["files"], reverse=True)
    return rows[:limit]


def key_files(folder, limit=10):
    """The files that explain the project, in priority order."""
    out = []
    for rel in _KEY_FILES:
        p = os.path.join(folder, rel.replace("/", os.sep))
        if os.path.exists(p):
            try:
                size = os.path.getsize(p) if os.path.isfile(p) else 0
            except Exception:
                size = 0
            out.append({"name": rel, "size": size})
        if len(out) >= limit:
            break
    return out


def entry_points(folder):
    """Best guess at how the project is started (script / main file)."""
    out = []
    pkg = os.path.join(folder, "package.json")
    if os.path.isfile(pkg):
        try:
            scripts = (json.loads(_read(pkg)) or {}).get("scripts") or {}
            for k in ("dev", "start", "build"):
                if k in scripts:
                    out.append(f"npm run {k}")
        except Exception:
            pass
    for f in ("app.py", "main.py", "manage.py", "index.js", "server.js"):
        if os.path.isfile(os.path.join(folder, f)):
            out.append(f"python {f}" if f.endswith(".py") else f"node {f}")
            break
    return out[:3]


def analyze(folder):
    """Full picture of a codebase → {languages, stack, structure, key_files,
    entry_points, files}. Returns {} when the folder isn't usable."""
    if not folder or not os.path.isdir(folder):
        return {}
    langs = languages(folder)
    return {
        "languages": langs,
        "primary": next(iter(langs), ""),
        "stack": stack(folder),
        "structure": structure(folder),
        "key_files": key_files(folder),
        "entry_points": entry_points(folder),
        "files": sum(langs.values()),
    }
