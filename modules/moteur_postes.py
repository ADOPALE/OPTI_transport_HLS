Le problème vient du fait que la fonction `preparer_flux_complets_du_jour(df_flux_brut, jour)` appelée à la ligne 289 de votre `app.py` filtre et transforme les colonnes du fichier de paramétrage brut.

Si l'on analyse l'export de votre fichier de paramétrage (`OptiFLUX_Parametres_20260615_AVEC_BIO_SC1.xlsx - M flux.csv`), la colonne contenant le point de départ s'appelle en réalité **`Site Expéditeur`** (ou `Site Destinataire` pour la destination), tandis que le moteur cherche la clé standardisée **`Origine`**. De plus, le dictionnaire retourné par le moteur doit être structuré de manière à ce que les fonctions d'affichage de l'interface (`rp.afficher_recap_jours`) puissent y accéder directement sans générer de conflits de clés.

Voici le code corrigé, robuste et tolérant aux variations de casse/nommage pour votre fichier **`modules/moteur_postes.py`** :

```python
import pandas as pd
import datetime as dt
import math

def str_to_min(time_str):
    """Convertit 'HH:MM:SS' ou 'HH:MM' en minutes depuis minuit avec gestion des types."""
    if pd.isna(time_str):
        return 0
    if isinstance(time_str, (dt.time, dt.datetime)):
        return time_str.hour * 60 + time_str.minute
    if isinstance(time_str, (int, float)):
        # Si c'est un float/int (ex: fraction de jour Excel), conversion en minutes
        if time_str <= 1.0:
            time_str = time_str * 24 * 60
        return int(time_str)
        
    time_str = str(time_str).strip()
    parts = list(map(int, time_str.split(':')))
    if len(parts) >= 2:
        return parts[0] * 60 + parts[1]
    return 0

def min_to_str(minutes):
    """Convertit des minutes en format 'HH:MM'."""
    h = int(minutes // 60) % 24
    m = int(minutes % 60)
    return f"{h:02d}:{m:02d}"

def optimiser_postes_jour(df_flux, df_vehicules, df_contenants=None, df_sites=None, 
                          matrice_duree=None, matrice_dist=None, params_log=None, 
                          nom_jour="Lundi", autoriser_tournees=True, budget_s=60, progress_cb=None):
    """
    MOTEUR DE PLANIFICATION ET D'ORDONNANCEMENT OPÉRATIONNEL :
    - Alignement complet avec les colonnes de l'application (Origine/Site Expéditeur, etc.)
    - Ouverture des postes du matin (S1) à 06h00 strict
    - Insertion automatique de la pause réglementaire au milieu de la tournée
    - Priorisation des urgences et corridors géographiques / métiers
    - Structuration du dictionnaire de sortie attendue par app.py & les modules de résultats
    """
    
    if progress_cb:
        try: progress_cb(0.1, f"Analyse et alignement des flux du {nom_jour}...")
        except: pass

    # Cartographie dynamique des colonnes pour s'adapter au fichier de paramètres ou au df filtré
    col_map = {}
    for c in df_flux.columns:
        c_clean = str(c).strip().lower()
        if c_clean in ['origine', 'site expéditeur', 'site expediteur', 'site_exp']:
            col_map['origine'] = c
        elif c_clean in ['destination', 'site destinataire', 'site_dest']:
            col_map['destination'] = c
        elif c_clean in ['fonction support', 'fonction_support', 'filière', 'filiere']:
            col_map['fonction_support'] = c
        elif c_clean in ['contenant', 'nature de contenant', 'type de contenant']:
            col_map['contenant'] = c
        elif 'urgence' in c_clean:
            col_map['urgent'] = c
        elif 'type véh' in c_clean or 'type veh' in c_clean:
            col_map['type_veh'] = c

    flux_list = []
    
    for idx, row in df_flux.iterrows():
        # Lecture avec valeurs par défaut si la colonne mappée est absente
        ori = row[col_map['origine']] if 'origine' in col_map else row.get('Origine', row.get('Site Expéditeur', 'HSJ'))
        dest = row[col_map['destination']] if 'destination' in col_map else row.get('Destination', row.get('Site Destinataire', 'HSJ'))
        f_support = row[col_map['fonction_support']] if 'fonction_support' in col_map else row.get('Fonction support', 'GENERAL')
        cont = row[col_map['contenant']] if 'contenant' in col_map else row.get('Contenant', '')
        
        # Identification de la quantité selon le jour ou la valeur générale
        qte = 1
        for q_col in [f'Quantité {nom_jour}', f'Quantite {nom_jour}', 'Quantité', 'Quantite', 'Volume']:
            if q_col in df_flux.columns and not pd.isna(row[q_col]):
                try:
                    qte = int(row[q_col])
                    break
                except:
                    pass

        # Gestion des fenêtres horaires de livraison
        h_dep_min = str_to_min(row.get('Heure de mise à disposition min départ', '06:00:00'))
        h_liv_max = str_to_min(row.get('Heure max de livraison à la destination', '21:00:00'))
        
        # Statut d'urgence
        is_urgent = False
        if 'urgent' in col_map:
            is_urgent = str(row[col_map['urgent']]).upper() in ['OUI', 'O', 'TRUE', '1']
        
        t_veh_req = row[col_map['type_veh']] if 'type_veh' in col_map else row.get('Type véh.', 'FOURGON')

        # Seuls les flux ayant un volume/quantité > 0 ce jour-là sont planifiés
        if qte > 0:
            flux_list.append({
                'id': row.get('Flux', f"FLUX_{idx}"),
                'origine': str(ori).strip(),
                'destination': str(dest).strip(),
                'contenant': cont,
                'quantite': qte,
                'fonction_support': str(f_support).strip(),
                'h_dep_min': h_dep_min,
                'h_liv_max': h_liv_max,
                'urgent': is_urgent,
                'type_veh_requis': str(t_veh_req).strip()
            })

    # Segmenter l'activité : Matin (S1: fenêtres démarrant avant 13h45) / Après-midi (S2)
    flux_matin = [f for f in flux_list if f['h_dep_min'] < 825]
    flux_apres_midi = [f for f in flux_list if f['h_dep_min'] >= 825]
    
    # Résolution de la liste des types de véhicules
    if isinstance(df_vehicules, pd.DataFrame):
        types_vehicules = df_vehicules['Types'].unique() if 'Types' in df_vehicules.columns else df_vehicules.index.unique()
    else:
        types_vehicules = ['FOURGON', 'HAILLON'] # Fallback de sécurité

    postes_finaux = []
    flux_non_servis = []
    suivi_flotte_max = {}

    total_types = len(types_vehicules)
    for idx_t, t_veh in enumerate(types_vehicules):
        if progress_cb:
            try: progress_cb(0.2 + 0.7 * (idx_t / total_types), f"Calcul du pavage pour {t_veh}...")
            except: pass

        flux_t_matin = [f for f in flux_matin if f['type_veh_requis'] == t_veh]
        flux_t_am = [f for f in flux_apres_midi if f['type_veh_requis'] == t_veh]
        
        if not flux_t_matin and not flux_t_am:
            continue
            
        # ─── OPTIMISATION DU PIC DU MATIN (S1 : DÉBARRAGE À 06:00 STRICT) ───
        n_matin = 1
        solution_matin_valide = False
        postes_matin_retenus = []
        
        while not solution_matin_valide and n_matin <= 40:
            tentative_postes = []
            for i in range(n_matin):
                tentative_postes.append({
                    'id': f"{t_veh}_S1_{i+1:02d}",
                    'type_veh': t_veh,
                    'veh_id': f"{t_veh}_VEH{i+1:02d}",
                    'temps_courant': 360, # 06:00 du matin
                    'position': 'HSJ',
                    'corridor_actuel': None,
                    'pause_prise': False,
                    'etapes': []
                })
            
            for p in tentative_postes:
                p['etapes'].append({
                    'Début': '06:00', 'Fin': '06:20', 'Durée (min)': 20,
                    'Étape': 'Prise de poste', 'Site départ': 'HSJ', 'Site arrivée': 'HSJ',
                    'Fonction support': 'RH'
                })
                p['temps_courant'] = 380
                
            flux_restants = list(flux_t_matin)
            echec_S1_SLA = False
            
            while flux_restants:
                meilleur_score = -1e9
                cible_poste = None
                cible_flux = None
                
                for p in tentative_postes:
                    if p['temps_courant'] >= 810: # 13:30 fin de vacation S1
                        continue
                        
                    # Insertion de la pause à mi-parcours (autour de 09h30 - 10h00)
                    if not p['pause_prise'] and p['temps_courant'] >= 550:
                        p['etapes'].append({
                            'Début': min_to_str(p['temps_courant']), 'Fin': min_to_str(p['temps_courant'] + 20),
                            'Durée (min)': 20, 'Étape': 'Pause (dépôt)', 'Site départ': p['position'], 'Site arrivée': 'HSJ',
                            'Fonction support': 'RH'
                        })
                        p['temps_courant'] += 20
                        p['position'] = 'HSJ'
                        p['pause_prise'] = True
                        continue

                    for f in flux_restants:
                        try:
                            t_approche = float(matrice_duree.at[p['position'], f['origine']])
                            t_mission = float(matrice_duree.at[f['origine'], f['destination']])
                        except:
                            t_approche, t_mission = 12, 15 # Forfaits par défaut si index absents
                            
                        t_manutention = 10
                        heure_livraison = p['temps_courant'] + t_approche + t_mission + t_manutention
                        
                        if heure_livraison > f['h_liv_max']:
                            continue # Le créneau horaire max est dépassé : rejet du planning courant
                            
                        # Système d'attribution de poids métiers
                        score = 0
                        if f['urgent']: score += 20000 # Priorité absolue aux urgences
                        if p['corridor_actuel'] == f['fonction_support']: score += 3000 # Synergies de corridors dédiés
                        score -= t_approche * 4 # Minimisation des kilomètres et transits à vide
                        
                        if score > meilleur_score:
                            meilleur_score = score
                            cible_poste = p
                            
                            cible_flux = f
                            
                if cible_poste and cible_flux:
                    p = cible_poste
                    f = cible_flux
                    try:
                        t_app = float(matrice_duree.at[p['position'], f['origine']])
                        t_mis = float(matrice_duree.at[f['origine'], f['destination']])
                    except:
                        t_app, t_mis = 12, 15
                        
                    if t_app > 0:
                        p['etapes'].append({
                            'Début': min_to_str(p['temps_courant']), 'Fin': min_to_str(p['temps_courant'] + t_app),
                            'Durée (min)': t_app, 'Étape': 'Approche à vide', 'Site départ': p['position'], 'Site arrivée': f['origine'],
                            'Fonction support': f['fonction_support']
                        })
                        p['temps_courant'] += t_app
                    
                    p['etapes'].append({
                        'Début': min_to_str(p['temps_courant']), 'Fin': min_to_str(p['temps_courant'] + t_mis + 10),
                        'Durée (min)': t_mis + 10, 'Étape': 'Mission (chargé)', 'Site départ': f['origine'], 'Site arrivée': f['destination'],
                        'Fonction support': f['fonction_support'], 'Flux concernés': str(f['id']), 'Contenants': f['contenant']
                    })
                    p['temps_courant'] += (t_mis + 10)
                    p['position'] = f['destination']
                    p['corridor_actuel'] = f['fonction_support']
                    
                    flux_restants.remove(f)
                else:
                    echec_S1_SLA = True
                    break
                    
            if echec_S1_SLA:
                n_matin += 1 # On incrémente le nombre de postes nécessaires le matin et on recommence
            else:
                for p in tentative_postes:
                    if not p['pause_prise']:
                        p['etapes'].append({
                            'Début': min_to_str(p['temps_courant']), 'Fin': min_to_str(p['temps_courant'] + 20),
                            'Durée (min)': 20, 'Étape': 'Pause', 'Site départ': p['position'], 'Site arrivée': p['position'], 'Fonction support': 'RH'
                        })
                        p['temps_courant'] += 20
                    if p['temps_courant'] < 810:
                        p['etapes'].append({
                            'Début': min_to_str(p['temps_courant']), 'Fin': '13:30', 'Durée (min)': 810 - p['temps_courant'],
                            'Étape': 'Clôture poste', 'Site départ': p['position'], 'Site arrivée': 'HSJ', 'Fonction support': 'RH'
                        })
                postes_matin_retenus = tentative_postes
                solution_matin_valide = True

        suivi_flotte_max[t_veh] = n_matin
        postes_finaux.extend(postes_matin_retenus)
        
        # ─── OPTIMISATION DE L'APRÈS-MIDI (S2 : INTERDICTION DE DÉPASSER LE PIC N_MATIN) ───
        n_am = 1
        solution_am_valide = False
        postes_am_retenus = []
        
        while not solution_am_valide and n_am <= n_matin:
            tentative_postes_am = []
            for i in range(n_am):
                tentative_postes_am.append({
                    'id': f"{t_veh}_S2_{i+1:02d}",
                    'type_veh': t_veh,
                    'veh_id': f"{t_veh}_VEH{i+1:02d}", # Réutilisation de la même flotte
                    'temps_courant': 825, # 13:45 début S2
                    'position': 'HSJ',
                    'corridor_actuel': None,
                    'pause_prise': False,
                    'etapes': []
                })
                
            for p in tentative_postes_am:
                p['etapes'].append({
                    'Début': '13:45', 'Fin': '14:05', 'Durée (min)': 20,
                    'Étape': 'Prise de poste', 'Site départ': 'HSJ', 'Site arrivée': 'HSJ',
                    'Fonction support': 'RH'
                })
                p['temps_courant'] = 845
                
            flux_restants_am = list(flux_t_am)
            echec_am_SLA = False
            
            while flux_restants_am:
                meilleur_score = -1e9
                cible_poste = None
                cible_flux = None
                
                for p in tentative_postes_am:
                    if p['temps_courant'] >= 1275: # 21:15 fin max S2
                        continue
                        
                    if not p['pause_prise'] and p['temps_courant'] >= 1010:
                        p['etapes'].append({
                            'Début': min_to_str(p['temps_courant']), 'Fin': min_to_str(p['temps_courant'] + 20),
                            'Durée (min)': 20, 'Étape': 'Pause (dépôt)', 'Site départ': p['position'], 'Site arrivée': 'HSJ',
                            'Fonction support': 'RH'
                        })
                        p['temps_courant'] += 20
                        p['position'] = 'HSJ'
                        p['pause_prise'] = True
                        continue

                    for f in flux_restants_am:
                        try:
                            t_approche = float(matrice_duree.at[p['position'], f['origine']])
                            t_mission = float(matrice_duree.at[f['origine'], f['destination']])
                        except:
                            t_approche, t_mission = 12, 15
                            
                        heure_livraison = p['temps_courant'] + t_approche + t_mission + 10
                        
                        if heure_livraison > f['h_liv_max']:
                            continue
                            
                        score = 0
                        if f['urgent']: score += 20000
                        if p['corridor_actuel'] == f['fonction_support']: score += 3000
                        score -= t_approche * 4
                        
                        if score > meilleur_score:
                            meilleur_score = score
                            cible_poste = p
                            cible_flux = f
                            
                if cible_poste and cible_flux:
                    p = cible_poste
                    f = cible_flux
                    try:
                        t_app = float(matrice_duree.at[p['position'], f['origine']])
                        t_mis = float(matrice_duree.at[f['origine'], f['destination']])
                    except:
                        t_app, t_mis = 12, 15
                        
                    if t_app > 0:
                        p['etapes'].append({
                            'Début': min_to_str(p['temps_courant']), 'Fin': min_to_str(p['temps_courant'] + t_app),
                            'Durée (min)': t_app, 'Étape': 'Approche à vide', 'Site départ': p['position'], 'Site arrivée': f['origine'],
                            'Fonction support': f['fonction_support']
                        })
                        p['temps_courant'] += t_app
                        
                    p['etapes'].append({
                        'Début': min_to_str(p['temps_courant']), 'Fin': min_to_str(p['temps_courant'] + t_mis + 10),
                        'Durée (min)': t_mis + 10, 'Étape': 'Mission (chargé)', 'Site départ': f['origine'], 'Site arrivée': f['destination'],
                        'Fonction support': f['fonction_support'], 'Flux concernés': str(f['id']), 'Contenants': f['contenant']
                    })
                    p['temps_courant'] += (t_mis + 10)
                    p['position'] = f['destination']
                    p['corridor_actuel'] = f['fonction_support']
                    
                    flux_restants_am.remove(f)
                else:
                    echec_am_SLA = True
                    break
                    
            if echec_am_SLA:
                if n_am < n_matin:
                    n_am += 1
                else:
                    # Règle absolue : Le plafond de l'après-midi correspond à la flotte du matin
                    flux_non_servis.extend(flux_restants_am)
                    break
            else:
                for p in tentative_postes_am:
                    if not p['pause_prise']:
                        p['etapes'].append({
                            'Début': min_to_str(p['temps_courant']), 'Fin': min_to_str(p['temps_courant'] + 20),
                            'Durée (min)': 20, 'Étape': 'Pause', 'Site départ': p['position'], 'Site arrivée': p['position'], 'Fonction support': 'RH'
                        })
                        p['temps_courant'] += 20
                    if p['temps_courant'] < 1275:
                        p['etapes'].append({
                            'Début': min_to_str(p['temps_courant']), 'Fin': '21:15', 'Durée (min)': 1275 - p['temps_courant'],
                            'Étape': 'Clôture poste', 'Site départ': p['position'], 'Site arrivée': 'HSJ', 'Fonction support': 'RH'
                        })
                postes_am_retenus = tentative_postes_am
                solution_am_valide = True
                
        postes_finaux.extend(postes_am_retenus)

    # 3. Génération des DataFrames finaux
    flat_etapes = []
    for p in postes_finaux:
        for e in p['etapes']:
            flat_etapes.append({
                'Jour': nom_jour,
                'Véhicule': p['veh_id'],
                'Poste': p['id'],
                'Type véh.': p['type_veh'],
                'Début': e['Début'],
                'Fin': e['Fin'],
                'Durée (min)': e['Durée (min)'],
                'Étape': e['Étape'],
                'Site départ': e['Site départ'],
                'Site arrivée': e['Site arrivée'],
                'Fonction support': e['Fonction support'],
                'Contenants': e.get('Contenants', ''),
                'Flux concernés': e.get('Flux concernés', '')
            })
            
    df_tournees = pd.DataFrame(flat_etapes) if flat_etapes else pd.DataFrame(columns=['Jour', 'Véhicule', 'Poste', 'Type véh.', 'Début', 'Fin', 'Durée (min)', 'Étape', 'Site départ', 'Site arrivée', 'Fonction support', 'Contenants', 'Flux concernés'])
    df_non_servis = pd.DataFrame(flux_non_servis) if flux_non_servis else pd.DataFrame(columns=['id', 'origine', 'destination', 'quantite'])
    
    if not df_non_servis.empty:
        df_non_servis['Pourquoi ce flux nest pas planifie'] = "Plafond capacitaire atteint l'après-midi"
        df_non_servis['CONTRAINTE BLOQUANTE'] = "Augmenter le nombre d'ouvertures de postes le matin"

    if progress_cb:
        try: progress_cb(1.0, f"Journée {nom_jour} calculée avec succès.")
        except: pass

    # Structure de sortie retournée pour l'intégration à l'interface globale
    return {
        'tournes': df_tournees,
        'non_servis': df_non_servis,
        'flotte_nb': suivi_flotte_max
    }

```
