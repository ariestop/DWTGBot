---
paths:
  - "deploy/scripts/**"
  - ".github/scripts/**"
---

# Bash-скрипты

Подробно: `docs/20-deployment.md`, `docs/24-runbooks.md`.

```bash
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=helpers.sh
source "${SCRIPT_DIR}/helpers.sh"     # включает set -Eeuo pipefail и ERR trap
```

- **Идемпотентность:** повторный запуск на здоровой системе ничего не
  ломает (`[[ -f … ]] && { log_ok "Already exists"; return; }`, `mkdir -p`).
- **Разрушительное — через подтверждение:** `confirm "Drop …?" "N" || die …`;
  `ASSUME_YES=1` для неинтерактивного режима и CI.
- **Логи — только хелперы:** `log_step`, `log_info`, `log_ok`, `log_warn`,
  `log_error`, `die`; не голый `echo`.
- Цель — Ubuntu 24.04 (`require_ubuntu_2404` перед версиезависимыми шагами).
- Кавычки всегда (`"${VAR}"`), массивы для аргументов, без `eval`,
  `mapfile -t` вместо `for f in $(ls …)`.
- Скрипты, которые запускает GitHub Actions, печатают в публичный лог:
  только обезличенный вывод (см. skill `server-ops`).
- Проверка: `bash -n` и `shellcheck --severity=warning` (так же в CI).
