#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Un processo solo per volta: il lucchetto che evita i duplicati che si rubano il lavoro.

Perché esiste (2026-09-22): Telegram consegna ogni update a **uno solo** dei poller
di un bot. Due driver avviati per sbaglio — succede: basta che il launcher parta
due volte, o che un doppio clic arrivi mentre il primo sta ancora partendo — non
danno nessun errore: si dividono i messaggi a metà, e dall'esterno sembra che
Aurora "non risponda a metà conversazione". Lo stesso vale per due ponti ComfyUI
sulla stessa scheda: due job in parallelo, tempi doppi.

Il lucchetto è un lock di sistema su un file, non un PID scritto dentro: se il
processo muore (anche ucciso con `Stop-Process`) il sistema operativo lo rilascia
da solo, quindi non restano lucchetti fantasma da cancellare a mano — che è il
difetto classico del file con dentro un PID.

Il file resta **leggibile**: contiene il PID di chi lo tiene, e nient'altro. Non è
banale: su Windows un lock di intervallo rende illeggibile la parte bloccata, e il
controllo è sulla lunghezza *richiesta* dalla lettura — non sui byte che esistono.
Per questo il byte bloccato sta a un mebibyte dall'inizio: una lettura normale
(4-8 KB, come fanno Python, PowerShell e `cat`) non lo tocca mai. Bloccandolo
vicino all'inizio, chi apriva il file per curiosità riceveva "un altro processo ha
bloccato una parte del file" — un errore che sembra un guasto mentre è tutto
regolare. Se invece serve il PID dei processi in corso, `start-surfaces.ps1 -Check`
lo legge dai processi veri (che è la fonte di verità).
"""
from __future__ import annotations

import os
from pathlib import Path


class AlreadyRunning(RuntimeError):
    """Un altro processo tiene già il lucchetto."""


# Il byte bloccato sta lontano dall'inizio (1 MiB): su Windows un lock di intervallo
# rende illeggibile la parte bloccata, e il controllo è sulla lunghezza RICHIESTA
# dalla lettura — non sui byte che esistono. A 4096 una lettura di 8 KB lo toccava e
# falliva; a un mebibyte nessuna lettura normale lo sfiora.
LOCK_OFFSET = 1 << 20


class SingleInstance:
    """Lucchetto esclusivo su un file, rilasciato dal sistema alla morte del processo.

    Uso:
        with SingleInstance("data/telegram-driver.lock", label="driver Telegram"):
            main()

    Il file viene creato se manca; la cartella anche. Il rilascio esplicito è
    gentile ma non necessario: se il processo sparisce, il lucchetto sparisce con
    lui.
    """

    def __init__(self, path, *, label: str = ""):
        self.path = Path(path)
        self.label = label or self.path.name
        self._file = None

    def acquire(self) -> "SingleInstance":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_bytes(b"")  # serve un file vero: si blocca un byte di quello
        handle = open(self.path, "r+b")
        # L'ordine conta: prima si prova il lucchetto, poi (se riesce) si svuota il
        # file, che potrebbe contenere il PID scritto da una versione precedente.
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(LOCK_OFFSET)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            handle.close()
            raise AlreadyRunning(
                f"{self.label}: già in esecuzione (lucchetto {self.path})") from e
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n".encode("utf-8"))
        handle.flush()
        self._file = handle
        return self

    def release(self) -> None:
        """Rilascia il lucchetto. Ripetuto, non fa niente."""
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(LOCK_OFFSET)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._file.close()
            self._file = None

    def __enter__(self) -> "SingleInstance":
        return self.acquire()

    def __exit__(self, *_eccezione) -> bool:
        self.release()
        return False
