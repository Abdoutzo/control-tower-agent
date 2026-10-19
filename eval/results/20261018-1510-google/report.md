# Évaluation du Control Tower Agent

- Date : 2026-10-18 15:10
- Modèle : google / gemini-flash-latest
- Questions évaluées : 6 sur 31

Notation automatique et déterministe (aucun LLM juge) : chaque question a des contrôles (chiffres attendus avec une tolérance, entités à citer, outils à appeler, livrable ou refus attendu), définis dans `eval/questions.json` à partir du corrigé caché.

## Résultat global : 6/6 questions entièrement réussies (100%), score moyen 100%

| Catégorie | Réussies | Score moyen | Objectif | Atteint |
|---|---|---|---|---|
| kpi | 6/6 | 100% | 90% | oui |

## Coût et rapidité

- Temps par question : médiane 30 s, max 113 s (inclut l'espacement imposé par la limite gratuite du fournisseur)
- Appels au modèle par question : moyenne 2.8
- Tokens par question : 12,612 en entrée, 1,188 en sortie (moyenne)
- Total du run : 75,670 tokens en entrée, 7,129 en sortie
- Réponses au format demandé (Réponse, Chiffres clés...) : 6/6

## Détail par question

| Id | Catégorie | Résultat | Score | Temps | Outils appelés | Contrôles échoués |
|---|---|---|---|---|---|---|
| Q01 | kpi | réussi | 100% | 20 s | data_overview, kpi |  |
| Q02 | kpi | réussi | 100% | 28 s | data_overview, kpi, detect_carrier_degradation |  |
| Q03 | kpi | réussi | 100% | 31 s | data_overview, kpi, analyze_premium_transport |  |
| Q04 | kpi | réussi | 100% | 17 s | data_overview, kpi |  |
| Q05 | kpi | réussi | 100% | 32 s | data_overview, kpi |  |
| Q06 | kpi | réussi | 100% | 113 s | kpi, data_overview, analyze_premium_transport |  |
