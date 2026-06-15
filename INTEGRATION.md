# Intégration — Refonte V3 du moteur transport (CHU Nantes / ADOPALE)

Ce paquet contient **uniquement les modules transport neufs ou modifiés**. Les
fichiers de la chaîne BIO et du socle restent **inchangés** dans ton dépôt
existant `OPTI_transport_HLS` : on ne les touche pas.

## 1. Fichiers fournis (à copier dans ton dépôt)

| Fichier | Action | Rôle |
|---|---|---|
| `modules/moteur_postes.py` | **remplace** | Nouveau moteur V3 (affectation au plus juste par surface, tournées bornées par durée max, marge inter-job, aléa de circulation, pavage + multi-start + recherche locale, relève par appariement, flux non servis explicites). |
| `modules/prep_transport.py` | **nouveau** | `preparer_flux_complets_du_jour(df_flux, jour)` — produit le `df_jour` harmonisé à partir du **M flux brut** (flux « Volume », quantité du jour > 0). |
| `modules/export_excel.py` | **nouveau** | `construire_excel(resultats, params)` — classeur 10 onglets (BytesIO). |
| `modules/resultats_postes.py` | **remplace** | Affichage Streamlit (récap hebdo, Gantt par véhicule, courbe de concurrence, quais, flux non servis). |
| `app.py` | **remplace / fusionne** | Onglet « Synthèse transport » réécrit : nouvelle signature, barre de progression, bouton d'export Excel. |

## 2. Fichiers à CONSERVER tels quels (déjà dans ton dépôt)

Chaîne BIO + socle, **ne pas modifier** :
`GeoMatrix.py`, `Import.py`, `check_flux.py`, `param_bio.py`, `biologie_engine.py`,
`resultats_bio.py`, `param_flux.py`, `Prep_simul_flux.py`, `__init__.py`.

## 3. Fichiers LEGACY à supprimer (nettoyage)

Devenus inutiles avec la refonte :
`biologie_engine_VI.py`, `sequencage_engine_VI.py`, `sim_engine_VI.py`,
`flux_engine.py`, `gantt_flux.py`, `sequencage_engine.py`,
`Resultats_simul_flux.py`, `sim_engine.py`.

⚠️ **Attention** : `app.py` fourni importe encore, en tête, `sim_engine`,
`sequencage_engine` et `Resultats_simul_flux` **uniquement** pour les anciens
onglets « Simul tournées » / « Distribution ». Si tu supprimes ces fichiers,
retire aussi ces imports et les onglets correspondants (ou garde ces fichiers
le temps de la transition). L'onglet **« Synthèse transport » ne dépend plus**
d'aucun fichier legacy : il n'utilise que `prep_transport`, `moteur_postes`,
`resultats_postes`, `export_excel`.

## 4. Changement de signature important

`optimiser_postes_jour` prend désormais la **matrice de distance** en 6ᵉ position :

```python
res = optimiser_postes_jour(
    df_jour, df_vehicules, df_contenants, df_sites,
    matrice_duree, matrice_distance,        # <-- NOUVEL ARGUMENT
    params_logistique, nom_jour="Lundi",
    autoriser_tournees=True, budget_s=60, n_starts=8,
    progress_cb=callback,                   # optionnel (barre de progression)
)
```

`params_logistique` doit contenir (déjà produits par `param_flux.py`) :
`rh` (`amplitude_totale`, `pause`, `h_prise_min`, `h_fin_max`, `temps_fixes_prise`,
`temps_fixes_fin`, `temps_releve`), `securite_remplissage`, `marge_inter_job`,
`duree_max_superjob`, `alea_circulation`, `vehicules_selectionnes`.

Le `df_jour` doit exposer : `Point de départ`, `Point de destination`,
`Nature de contenant`, `Quantite_du_jour`, `Type (propre/sale)`,
`Fonction Support associée`, `Heure de mise à disposition min départ`,
`Heure max de livraison à la destination`. C'est exactement ce que renvoie
`prep_transport.preparer_flux_complets_du_jour`.

## 5. Résultat renvoyé

```python
{
 "postes": [...],        # list[Poste] : .etapes, .missions, .id_vehicule, .h_debut/h_fin, .occupation()
 "missions": [...],      # list[Mission] : .composantes [(flux_id,orig,dest,cont,qte)], .fill, .sens
 "non_servis": [...],    # [{flux_id, origine, destination, contenant, raison, contrainte}]
 "nb_vehicules": {...},  # {type: nb}
 "metriques": {...},     # KPI (flotte, postes, km vide/plein, occupation, pic quais, temps_calcul_s)
 "concurrence": {...}, "quais": {...}, "jour": ..., "depot": ..., "shifts": [...]
}
```

## 6. Lecture du dimensionnement (diagnostic honnête)

Sur les données réelles (semaine type), la flotte ressort autour de **18-20
véhicules** les jours chargés, en **respectant toutes les fenêtres horaires**.
Ce chiffre n'est pas réductible sans concession : la **pause + relève** impose
que deux postes d'un même véhicule soient **disjoints** (matin / après-midi),
or étaler les postes pour baisser la pointe **détruit** l'appariement de relève.
Le moteur co-optimise donc placement horaire **et** relève simultanément.
Les seuls flux non planifiés sont des **incohérences de données** (fenêtre de
livraison avant mise à disposition, ou durée de mission supérieure à la fenêtre)
— détaillées avec la contrainte bloquante dans l'onglet « Flux non servis ».

## 7. Dépendance

`export_excel.py` nécessite **openpyxl** (déjà dans `requirements.txt`).
