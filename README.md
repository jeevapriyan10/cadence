# Cadence

> *It doesn't predict delays. It decides when the track is free.*

**Status: under active development**

Cadence is a railway block-window scheduling engine designed to compute conflict-free, optimal track occupancies and maintenance windows across complex rail networks.

---

## Architecture

*(Placeholder — architectural diagrams, solver models, and system design specifications will be documented here as development proceeds.)*

---

## Repository Structure

```text
cadence/
├── .github/
│   └── workflows/
│       └── ci.yml             # GitHub Actions CI workflow
├── backend/                   # Python 3.11 FastAPI backend
│   ├── cadence/               # Core engine package
│   ├── tests/                 # Backend unit & integration test suite
│   ├── .env.example           # Example environment variables
│   ├── pyproject.toml         # Package metadata and build configuration
│   └── requirements.txt       # Dependencies (fastapi, ortools, networkx, etc.)
├── frontend/                  # React + TypeScript + Vite web interface
│   ├── src/                   # Application source
│   ├── package.json           # Frontend dependencies and scripts
│   └── vite.config.ts         # Vite configuration
├── .gitignore
├── LICENSE                    # MIT License
└── README.md
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 20+ and npm

### Backend Setup
```bash
cd backend
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Unix/macOS:
source .venv/bin/activate

pip install -r requirements.txt
pytest
```

### Frontend Setup
```bash
cd frontend
npm install
npm test
npm run dev
```

---

## License

[MIT](LICENSE) © 2026 jeevapriyan10