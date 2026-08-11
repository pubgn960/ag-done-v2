# Contributing to Telegram Email Image Delivery Bot

Thank you for your interest in contributing to the **Telegram Email Image Delivery Bot**! We welcome contributions, bug reports, and feature requests.

---

## 🛠 Development Workflow

### 1. Fork and Clone
```bash
git clone https://github.com/YOUR-USERNAME/telegram-email-delivery-bot.git
cd telegram-email-delivery-bot
```

### 2. Branching Strategy
We adhere to Git Flow conventions:
- `main` - Stable production code.
- `develop` - Active integration branch.
- `feature/*` - New features or extensions.
- `bugfix/*` - Fixes for identified issues.

Create your feature branch from `develop`:
```bash
git checkout -b feature/my-new-feature
```

### 3. Environment Setup
```bash
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### 4. Code Guidelines
- **PEP 8**: Follow standard Python formatting and naming conventions.
- **Type Hints**: Annotate function signatures with standard Python typing.
- **Security**: Never hardcode credentials, tokens, or group IDs.
- **Testing**: Run local tests prior to committing:
  ```bash
  python -m unittest discover -s tests
  ```

---

## 📝 Submitting Pull Requests

1. Commit your changes with clear, descriptive commit messages.
2. Push your branch to GitHub:
   ```bash
   git push origin feature/my-new-feature
   ```
3. Open a Pull Request targeting the `develop` branch.
4. Ensure all GitHub Actions CI checks pass.
