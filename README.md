# OptiFLUX - moteur de distribution corrige

Cette version remplace la chaine active de distribution :

`app.py -> prep_transport.py -> moteur_postes.py -> resultats_postes.py / export_excel.py`

## Corrections principales

- Tous les flux positifs sont obligatoires, y compris les quantites fractionnaires.
- Le taux maximal d'occupation du plancher est une contrainte dure.
- Aucun regroupement ne peut depasser la capacite 2D ou la capacite du contenant.
- Chaque insertion dans une tournee groupee recalcule :
  - le vehicule compatible ;
  - la charge ;
  - le meilleur ordre local de desserte ;
  - la fenetre de depart compatible avec les sous-missions.
- Les flux interdisant le transport mixte restent isoles.
- Le temps inter-job parametre est integre entre deux missions.
- Tous les postes durent exactement la duree parametree, soit 450 minutes par defaut.
- Le temps sans activite en fin de poste est affiche comme `DISPONIBLE`.
- L'objectif privilegie successivement :
  1. la flotte ;
  2. le nombre de postes ;
  3. le nombre de postes occupes a moins de 80 % ;
  4. le deficit d'occupation ;
  5. l'homogeneite.
- Un audit final independant bloque la validite en cas de flux absent, surcharge,
  retard, chevauchement ou mauvaise amplitude.

## Fichiers a remplacer

- `app.py`
- `modules/moteur_postes.py`
- `modules/param_flux.py`
- `modules/resultats_postes.py`
- `modules/export_excel.py`

Les autres fichiers sont fournis afin de conserver une arborescence complete.

## Resultat du test fourni

Le fichier `dimensionnement_transport_corrige.xlsx` est genere avec le fichier
de parametrage du 15 juin 2026.

Les resultats sont techniquement coherents, mais restent non valides tant que
les deux flux bloques par jour ne peuvent pas etre servis dans leurs fenetres
horaires. Ils sont listes dans l'onglet `7. Flux NON servis`.

Le moteur global utilise une heuristique multi-start avec recherche locale.
Les ordres de desserte des petits regroupements sont optimises exactement par
enumeration locale. Il ne fournit pas de preuve mathematique d'optimalite globale.
