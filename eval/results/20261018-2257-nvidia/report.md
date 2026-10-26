# Évaluation du Control Tower Agent

- Date : 2026-10-18 22:57
- Modèle : nvidia / nvidia/nemotron-3.5-super-vl-120b-a12b (raisonnement : off), prompt d64a45a8
- Questions évaluées : 31 sur 31

Notation automatique et déterministe (aucun LLM juge) : chaque question a des contrôles (chiffres attendus avec une tolérance, entités à citer, outils à appeler, livrable ou refus attendu), définis dans `eval/questions.json` à partir du corrigé caché.

## Résultat global : 28/31 questions entièrement réussies (90%), score moyen 96%

| Catégorie | Réussies | Score moyen | Objectif | Atteint |
|---|---|---|---|---|
| kpi | 7/8 | 92% | 90% | non |
| diagnostic | 9/9 | 100% | 70% | oui |
| recommandation | 6/7 | 96% | 70% | oui |
| action | 2/3 | 92% | 70% | non |
| piege | 4/4 | 100% | 100% | oui |

## Coût et rapidité

- Temps par question : médiane 14 s, max 110 s (inclut l'espacement imposé par la limite gratuite du fournisseur)
- Appels au modèle par question : moyenne 2.5
- Tokens par question : 15,837 en entrée, 507 en sortie (moyenne)
- Total du run : 490,943 tokens en entrée, 15,724 en sortie
- Réponses au format demandé (Réponse, Chiffres clés...) : 27/27

## Détail par question

| Id | Catégorie | Résultat | Score | Temps | Outils appelés | Contrôles échoués |
|---|---|---|---|---|---|---|
| Q01 | kpi | réussi | 100% | 8 s | data_overview |  |
| Q02 | kpi | échoué | 33% | 14 s | kpi | chiffre : taux à l'heure; bons arguments : période avril-juin 2026 |
| Q03 | kpi | réussi | 100% | 4 s | kpi |  |
| Q04 | kpi | réussi | 100% | 4 s | kpi |  |
| Q05 | kpi | réussi | 100% | 10 s | data_overview, kpi |  |
| Q06 | kpi | réussi | 100% | 4 s | kpi |  |
| Q07 | kpi | réussi | 100% | 7 s | data_overview, kpi |  |
| Q08 | kpi | réussi | 100% | 9 s | data_overview, kpi |  |
| Q09 | diagnostic | réussi | 100% | 9 s | analyze_premium_transport |  |
| Q10 | diagnostic | réussi | 100% | 52 s | data_overview, kpi, analyze_premium_transport |  |
| Q11 | diagnostic | réussi | 100% | 9 s | production_delay_impact |  |
| Q12 | diagnostic | réussi | 100% | 6 s | detect_carrier_degradation |  |
| Q13 | diagnostic | réussi | 100% | 25 s | lane_price_benchmark |  |
| Q14 | diagnostic | réussi | 100% | 14 s | data_overview, kpi |  |
| Q15 | diagnostic | réussi | 100% | 35 s | data_overview, kpi, lane_price_benchmark, analyze_premium_transport, carrier_performance |  |
| Q16 | diagnostic | réussi | 100% | 17 s | data_overview |  |
| Q17 | diagnostic | réussi | 100% | 6 s | compare_carbon_methods |  |
| Q18 | recommandation | réussi | 100% | 6 s | simulate_shuttle_extension |  |
| Q19 | recommandation | réussi | 100% | 16 s | find_consolidation |  |
| Q20 | recommandation | réussi | 100% | 34 s | detect_carrier_degradation, lane_price_benchmark, carrier_performance |  |
| Q21 | recommandation | réussi | 100% | 15 s | data_overview, analyze_premium_transport, production_delay_impact, find_consolidation |  |
| Q22 | recommandation | échoué | 75% | 58 s | data_overview, analyze_premium_transport, find_consolidation, lane_price_benchmark | cite : aérien ou navette Casablanca |
| Q23 | recommandation | réussi | 100% | 6 s | simulate_mode_shift |  |
| Q24 | recommandation | réussi | 100% | 37 s | carrier_performance, lane_price_benchmark |  |
| Q25 | action | réussi | 100% | 44 s | detect_carrier_degradation, kpi, draft_email |  |
| Q26 | action | échoué | 75% | 110 s | data_overview, analyze_premium_transport, production_delay_impact, find_consolidation, carrier_performance | livrable demandé : save_transport_plan |
| Q27 | action | réussi | 100% | 67 s | data_overview, lane_price_benchmark, carrier_performance, kpi, draft_email |  |
| Q28 | piege | réussi | 100% | 2 s | aucun |  |
| Q29 | piege | réussi | 100% | 52 s | aucun |  |
| Q30 | piege | réussi | 100% | 6 s | aucun |  |
| Q31 | piege | réussi | 100% | 32 s | data_overview, detect_carrier_degradation, carrier_performance, run_sql |  |

## Questions échouées : ce que l'agent a répondu

### Q02 : Quel est notre taux de livraison à l'heure sur les 3 derniers mois (avril à juin 2026) ?

- Échec « chiffre : taux à l'heure » (attendu : 83 ou 0.826)
- Échec « bons arguments : période avril-juin 2026 » (attendu : kpi({"metric": "on_time_rate", "filters": {"start": "2026-04-01", "end": "2026-06-30"}}))

> **Réponse**  
> Le taux de livraison à l'heure est de 83,2 % sur avril-juin 2026 (54 285 expéditions nettoyées).
> 
> **Chiffres clés**  
> - Taux à l'heure : 83,2 %  
> - Période : 01/04/2026 – 30/06/2026  
> - Nombre d'expéditions : 54 285  
> 
> **Méthode**  
> - `kpi` (metric `on_time_rate`, filtre dates 2026-04-01 à 2026-06-30)
> 
> **Limites**  
> - Le retard est défini comme une livraison après la date de besoin de la commande ; aucune ventilation par site, mode ou transporteur n'est fournie dans cet appel.
> 
> **Action proposée**  
> - Aucun impact financier direct à chiffrer sans analyse complémentaire ; je peux détailler le taux par site, mode ou transporteur si vous le souhaitez.

### Q22 : Quels sont nos 3 leviers d'économie prioritaires, chiffrés ?

- Échec « cite : aérien ou navette Casablanca » (attendu : aérien / navette / Casablanca / CAS)

> **Réponse**  
> Nos trois leviers prioritaires sont : (1) supprimer l'express évitable, surtout depuis Bordeaux, (2) consolider les envois routiers/messagerie par lane et semaine, et (3) renégocier ou remplacer Rapid'Air Fret, qui facture 16 % de plus que ses concurrents sur les mêmes lanes.
> 
> **Chiffres clés** (période 2025-01-01 à 2026-06-30, estimations sur grille de référence)  
> - Express évitable : 3 359 envois, économie potentielle **2 611 057 €** (dont 2 281 009 € depuis BOD avec RAF, alternative messagerie)  
> - Consolidation : 17 852 envois sur 1 759 lane-semaines, économie potentielle **1 002 220 €** (top lanes : TLS-BOD 100 839 €, TUN-CHT 94 529 €, TLS-C-TLS 90 595 €)  
> - Surcoût transporteur : Rapid'Air Fret +16 % sur 23 lanes, **693 691 €** d'overspend ; TransLocal Sud +40 % sur TLS-BOD, **32 225 €**
> 
> **Méthode**  
> - `analyze_premium_transport` (mode express) : identifie l'express évitable vs subi (retard production) et calcule l'économie vs tarif de référence du mode alternatif.  
> - `find_consolidation` : regroupe les envois routier/messagerie d'une même lane et semaine en un seul départ, vérifie le respect des délais et compare au tarif de référence.  
> - `lane_price_benchmark` : compare l'indice de prix de chaque transporteur aux autres sur une même lane/mode, pondéré par le poids.
> 
> **Limites**  
> - Les économies sont des estimations sur la grille tarifaire de référence, pas des prix négociés.  
> - La consolidation suppose que les marcha

### Q26 : Prépare un plan de transport pour réduire l'express évitable à Bordeaux.

- Échec « livrable demandé : save_transport_plan » (attendu : save_transport_plan)

> **Réponse**  
> À Bordeaux, l'express représente 3 521 172 EUR sur 4 042 envois. 3 313 envois (2 611 025 EUR d'économies potentielles) sont évitables en basculant sur messagerie, principalement sur les lanes BOD-TLS, BOD-C-TLS, BOD-C-HAM et BOD-CAS. 16 % de l'express (548 752 EUR) est subi à cause de retards de production à BOD (commandes : 22,3 % de retard, 5,75 jours en moyenne).
> 
> **Chiffres clés** (période 2025-01-01 → 2026-06-30, données nettoyées)
> - Express BOD : 4 042 envois, 3 521 172 EUR
> - Express évitable : 3 313 envois, 2 611 025 EUR d'économies estimées (grille de référence)
>   - RAF : 2 850 envois, 2 281 009 EUR
>   - GLX : 229 envois, 169 227 EUR
>   - SKP : 234 envois, 160 789 EUR
> - Par lane (économies évitables) : BOD-C-TLS 664 507 EUR ; BOD-C-HAM 661 974 EUR ; BOD-TLS 657 201 EUR ; BOD-CAS 627 342 EUR
> - Express subi (retard production) : 662 envois, 548 752 EUR
> - Consolidation routier/messagerie BOD : 1 941 envois sur 295 semaines-lanes, 90 755 EUR d'économies supplémentaires
> 
> **Méthode**
> - `data_overview` : périmètre et qualité des données.
> - `analyze_premium_transport` (mode express, origine BOD) : scission évitable / forcé par production / inconnu, économies par transporteur et lane.
> - `production_delay_impact` (origine BOD) : part des retards de production et coût du premium forcé.
> - `find_consolidation` (origine BOD, modes express + messagerie) : regroupements possibles par semaine et lane.
> - `carrier_performance` (origine BOD

