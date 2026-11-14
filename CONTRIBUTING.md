# Contributing to Groww Algo Trading

Thank you for your interest in contributing! We welcome improvements, bug reports, and new trading strategies.

---

## Contribution Workflow

To contribute to this repository:

1. **Fork the Repository**:
   Click the "Fork" button at the top right of this repository's GitHub page.

2. **Clone your Fork**:
   ```bash
   git clone https://github.com/<your-username>/groww-algo-trading.git
   cd groww-algo-trading
   ```

3. **Create a Feature Branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

4. **Make Your Changes**:
   - Write clean, well-documented code.
   - Follow PEP 8 guidelines.
   - Do not commit sensitive keys, real broker credentials, or personal token data.

5. **Run the Test Suite**:
   Verify that all automated tests pass before committing:
   ```bash
   pytest
   ```
   Add new unit tests under `tests/` for any new functionality or bug fixes.

6. **Submit a Pull Request (PR)**:
   - Push your feature branch to your fork on GitHub.
   - Open a Pull Request against the `main` branch of this repository.
   - Describe what changed and provide test results.

---

## Review and Merge Process

- Direct pushes to the `main` branch are restricted.
- Every contribution goes through a Pull Request review.
- Automated CI tests run against every PR.
- Changes will only be merged into the project after maintainer review and explicit approval.
