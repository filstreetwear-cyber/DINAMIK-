# Development-tooling frameworks — SWE-agent, Deep Agents, Agno (Milestone 8.5)

A differenza di Playwright MCP/Browser Use (`browser-abstraction.md`), qui
non è stato fatto un install+smoke-test pratico di tutti e tre, e vale la
pena dire perché prima dei risultati, non dopo: per i due browser backend
la domanda era tecnica e verificabile ("funziona questo motore contro il
Chromium reale del sandbox?"), con un'alternativa concreta
(`BrowserAutomationEngine`) da confrontare. Per questi tre, la domanda che
la specifica pone è diversa — "aiuterebbero a **costruire** Dinamik più
velocemente?" — e la risposta dipende dal processo di lavoro reale di
questo progetto (sessioni Claude Code dirette, check→conferma→procedi per
fase, con Fil che rivede ogni milestone), non da un test tecnico che un
`pip install` potrebbe illuminare. I metadati reali qui sotto (versione,
dipendenze, scopo dichiarato) sono comunque presi da PyPI in questa
sessione, non da memoria.

Vincolo esplicito della specifica, rispettato in ogni caso: **nessuno dei
tre diventa una dipendenza runtime** — `pyproject.toml` non cambia per
nessuno di questi tre, indipendentemente dalla decisione.

## SWE-agent (Princeton NLP)

PyPI: `sweagent`, versione reale trovata `0.0.1`. Scopo dichiarato: "an
open source Agent Computer Interface for running language models as
software engineers" — un agente autonomo che risolve issue/bug reali
dato accesso a un repository (il progetto originale dietro SWE-bench).

**Decisione: REJECT per l'uso attuale.** Non per un limite tecnico — è
letteralmente un agente che fa quello che questa sessione sta già
facendo (leggere un repo, modificare codice, verificare, aprire un
cambiamento) ma **senza** il checkpoint umano che governa ogni fase di
questo progetto dal Phase A in poi. Introdurlo significherebbe avere due
agenti di sviluppo autonomi sullo stesso repository, con solo uno dei due
sotto la disciplina "check → conferma → procedi" che Fil ha imposto e
mantenuto per l'intero progetto. Non risolve un problema che questo
progetto ha oggi.

## Deep Agents (`deepagents`, LangChain)

PyPI: versione reale trovata `0.7.15`. Scopo dichiarato: "Production-ready,
extensible agent harness with a built-in filesystem and context
management, sub-agent delegation, skills, and long-term memory" — un
framework general-purpose per costruire agenti multi-step con
sotto-agenti, modellato esplicitamente sullo stesso pattern di Claude
Code. Dipendenze reali: l'intero stack LangChain (`langchain`,
`langchain-core`, `langchain-anthropic`, `langchain-google-genai`,
`langsmith`) — non leggero.

**Decisione: EVALUATE_LATER, non ora.** È l'unico dei tre con un fit
concettuale reale su qualcosa che questo progetto costruirà davvero —
Milestone 11 (Agent orchestration, non ancora iniziata) dovrà coordinare
più agenti (Amazon Scout oggi, Amazon Auditor/Sales Copilot in roadmap,
per README). Ma quella è una decisione di **Milestone 11**, con il suo
proprio audit quando ci si arriva — deciderla ora, dentro Milestone 8.5,
significherebbe impegnarsi sull'intero stack LangChain come dipendenza
runtime futura senza che Milestone 9/10 (scoring, AI layer) siano
nemmeno iniziate. Prematuro, non sbagliato.

## Agno (`agno`, ex Phidata)

PyPI: versione reale trovata `3.0.10`. Si descrive come "the programming
language for agentic software" — un framework più leggero di Deep
Agents per costruire sistemi multi-agente con tool/memoria propri.

**Decisione: EVALUATE_LATER, stesso motivo di Deep Agents.** Stesso
ragionamento: un candidato plausibile per Milestone 11, non una
decisione da prendere dentro l'audit di Milestone 8.5. Le due valutazioni
(Deep Agents vs Agno, se e quando Milestone 11 viene affrontata) vanno
fatte insieme, non una alla volta ora — sono soluzioni allo stesso
problema (orchestrazione multi-agente), confrontarle isolatamente
in momenti diversi del progetto porterebbe a una scelta meno informata.

## Riepilogo decisioni

| Framework | Decisione | Trigger per rivalutare |
|---|---|---|
| SWE-agent | REJECT | Nessuno identificato — sostituirebbe la disciplina check→conferma→procedi di questo progetto, non la migliora |
| Deep Agents | EVALUATE_LATER | Quando Milestone 11 (Agent orchestration) viene affrontata — insieme ad Agno, non prima |
| Agno | EVALUATE_LATER | Come sopra |
