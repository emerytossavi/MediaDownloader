# Exemple Pratique : Cycle TDD en Python (Red-Green-Refactor)

Ce guide illustre un cycle TDD complet sur un composant représentatif : un **limiteur de débit par utilisateur (`RateLimiter`)**.

---

## 1. Cycle 1 : Autoriser une première requête

### Étape 1.1 : RED (Écriture du premier test)
```python
# tests/test_rate_limiter.py
from app.services.rate_limiter import RateLimiter

def test_rate_limiter_allows_first_request_for_user():
    limiter = RateLimiter(max_requests=2, window_seconds=60)
    user_id = 12345
    
    assert limiter.is_allowed(user_id) is True
```
*Exécution : `pytest tests/test_rate_limiter.py` ➜ **Échec (ModuleNotFoundError ou AttributeError)***.

### Étape 1.2 : GREEN (Code minimal)
```python
# app/services/rate_limiter.py
class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def is_allowed(self, user_id: int) -> bool:
        return True
```
*Exécution : `pytest tests/test_rate_limiter.py` ➜ **Succès (Vert)***.

---

## 2. Cycle 2 : Bloquer quand la limite est atteinte

### Étape 2.1 : RED (Ajout du test de dépassement)
```python
# tests/test_rate_limiter.py
def test_rate_limiter_blocks_requests_exceeding_limit():
    limiter = RateLimiter(max_requests=2, window_seconds=60)
    user_id = 12345
    
    assert limiter.is_allowed(user_id) is True
    assert limiter.is_allowed(user_id) is True
    assert limiter.is_allowed(user_id) is False  # La 3e requête doit être rejetée
```
*Exécution : `pytest tests/test_rate_limiter.py` ➜ **Échec (assert True is False)***.

### Étape 2.2 : GREEN (Implémentation minimale)
```python
# app/services/rate_limiter.py
import time
from collections import defaultdict

class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._history = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        now = time.time()
        timestamps = self._history[user_id]
        
        # Filtrer les timestamps hors fenêtre
        valid_timestamps = [t for t in timestamps if now - t < self.window_seconds]
        
        if len(valid_timestamps) >= self.max_requests:
            return False
            
        valid_timestamps.append(now)
        self._history[user_id] = valid_timestamps
        return True
```
*Exécution : `pytest tests/test_rate_limiter.py` ➜ **Succès (Vert)***.

---

## 3. Cycle 3 : REFACTOR (Découplage temporel via injection)

### Constat lors du Refactoring :
Utiliser directement `time.time()` rend difficile le test du passage du temps sans faire de vrais `time.sleep()`.  
On injecte donc une fonction d'horloge (*Clock abstraction*) :

```python
# tests/test_rate_limiter.py
def test_rate_limiter_resets_quota_after_window_expires():
    current_time = 1000.0
    def fake_clock():
        return current_time

    limiter = RateLimiter(max_requests=1, window_seconds=60, clock=fake_clock)
    user_id = 12345
    
    assert limiter.is_allowed(user_id) is True
    assert limiter.is_allowed(user_id) is False
    
    # Avancer le temps de 61 secondes
    current_time += 61.0
    assert limiter.is_allowed(user_id) is True
```

```python
# app/services/rate_limiter.py (Code propre et testable)
import time
from collections import defaultdict
from typing import Callable

class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int, clock: Callable[[], float] = time.time):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._history: dict[int, list[float]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        now = self._clock()
        cutoff = now - self.window_seconds
        
        # Nettoyage glissant
        self._history[user_id] = [t for t in self._history[user_id] if t > cutoff]
        
        if len(self._history[user_id]) >= self.max_requests:
            return False
            
        self._history[user_id].append(now)
        return True
```
*Exécution : Tous les tests passent instantanément sans aucun mock complexe ni `time.sleep()`.*
