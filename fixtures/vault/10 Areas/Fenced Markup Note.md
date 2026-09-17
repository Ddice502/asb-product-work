# Fenced Markup Note

Prose before the fence that is long enough to clear the readable signal gate.

```bash
set -euo pipefail
# rotate the credential before the deploy
ROTATE_TOKEN=placeholder
---
<!-- the rule above is part of the printed banner -->
echo done
```

Prose between the fence and the rule, also long enough to be indexed here.

<!-- a pipeline marker outside the fence, which must be stripped -->

---

Prose after the rule, which must survive and must end the span.

````
QUADSECRET <!-- QUADMARKER --> sits inside a four-backtick fence.
````

Final prose line so the span still ends on real text here.
