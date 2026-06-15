# OptiFLUX - version contraintes dures

Cette version applique les règles suivantes comme contraintes bloquantes :

- tous les flux actifs doivent être servis et leurs quantités réconciliées ;
- aucune fenêtre horaire n'est relâchée ;
- chaque poste dure exactement la durée paramétrée, soit 450 minutes par défaut ;
- le temps inter-job paramétré est présent entre chaque paire de missions ;
- le taux maximal d'occupation du véhicule est strictement respecté ;
- chaque insertion dans une tournée regroupée recalcule le meilleur ordre
  compatible des arrêts et les fenêtres de la super-mission ;
- l'objectif minimise successivement la flotte, le nombre de postes, puis le
  nombre de postes sous le seuil d'occupation paramétré.

Une solution qui viole une contrainte dure est explicitement déclarée invalide
dans l'application et dans l'export Excel.

Le moteur effectue une recherche multi-départs bornée par le budget de calcul.
Il fournit la meilleure solution trouvée selon l'ordre lexicographique
flotte, postes, postes sous le seuil, mais ne prétend pas fournir une preuve
mathématique d'optimalité globale.

## Lancement

```bash
streamlit run app.py
```
