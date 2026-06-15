import pandas as pd
import datetime as dt
import math

def str_to_min(time_str):
    """Convertit 'HH:MM:SS' ou 'HH:MM' en minutes depuis minuit."""
    if pd.isna(time_str) or not isinstance(time_str, str):
        return 0
    parts = list(map(int, time_str.split(':')))
    if len(parts) >= 2:
        return parts[0] * 60 + parts[1]
    return 0

def min_to_str(minutes):
    """Convertit des minutes en format 'HH:MM'."""
    h = int(minutes // 60) % 24
    m = int(minutes % 60)
    return f"{h:02d}:{m:02d}"

def optimiser_postes_jour(df_flux, df_vehicules, df_rh, matrice_duree, matrice_dist, jour="Lundi"):
    """
    Moteur de séquençage et d'ordonnancement conforme :
    - Ouverture stricte à 06:00 pour le Matin (S1)
    - Pause obligatoire insérée dynamiquement au milieu du poste (+/- 1h)
    - Priorité absolue aux urgences (SLA strict)
    - Affectation préférentielle par Corridor / Fonction Support (camions dédiés)
    - Minimisation des trajets à vide et attentes
    - Si un créneau explose -> Recommence à zéro avec N+1 postes le matin
    - Après-midi (S2) limité strictement au nombre de postes du matin (N_S1)
    """
    
    flux_list = []
    for idx, row in df_flux.iterrows():
        h_dep_min = str_to_min(row.get('Heure de mise à disposition min départ', '06:00:00'))
        h_liv_max = str_to_min(row.get('Heure max de livraison à la destination', '21:00:00'))
        
        flux_list.append({
            'id': row.get('Flux', idx),
            'origine': row['Origine'],
            'destination': row['Destination'],
            'contenant': row.get('Contenant', row.get('Nature de contenant', '')),
            'quantite': int(row.get('Quantité', row.get(f'Quantité {jour}', 1))),
            'fonction_support': row.get('Fonction support', row.get('Fonction Support associée', 'GENERAL')),
            'h_dep_min': h_dep_min,
            'h_liv_max': h_liv_max,
            'urgent': str(row.get('Urgence / flux prioritaire \n(Oui/Non)', row.get('Urgence', 'NON'))).upper() in ['OUI', 'O'],
            'type_veh_requis': row.get('Type véh.', 'FOURGON')
        })

    # Séparation temporelle : Matin (S1 avant 13h45) vs Après-midi (S2 après 13h45)
    flux_matin = [f for f in flux_list if f['h_dep_min'] < 825]
    flux_apres_midi = [f for f in flux_list if f['h_dep_min'] >= 825]
    
    types_vehicules = df_vehicules['Types'].unique() if 'Types' in df_vehicules.columns else df_vehicules.index.unique()
    
    postes_finaux = []
    flux_non_servis = []
    suivi_flotte_max = {}

    for t_veh in types_vehicules:
        flux_t_matin = [f for f in flux_matin if f['type_veh_requis'] == t_veh]
        flux_t_am = [f for f in flux_apres_midi if f['type_veh_requis'] == t_veh]
        
        if not flux_t_matin and not flux_t_am:
            continue
            
        # ─── SECTION 1 : VOYAGE DU MATIN (DÉBUTE À 06:00) ───
        n_matin = 1
        solution_matin_valide = False
        postes_matin_retenus = []
        
        while not solution_matin_valide and n_matin <= 50:
            tentative_postes = []
            for i in range(n_matin):
                tentative_postes.append({
                    'id': f"{t_veh}_S1_{i+1:02d}",
                    'type_veh': t_veh,
                    'veh_id': f"{t_veh}_VEH{i+1:02d}",
                    'temps_courant': 360,
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
                    if p['temps_courant'] >= 810:
                        continue
                        
                    # RÈGLE DE LA PAUSE : Déclenchement automatique autour du milieu de poste
                    if not p['pause_prise'] and p['temps_courant'] >= 540:
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
                            t_approche, t_mission = 15, 15
                            
                        t_manutention = 10
                        heure_livraison = p['temps_courant'] + t_approche + t_mission + t_manutention
                        
                        if heure_livraison > f['h_liv_max']:
                            continue
                            
                        score = 0
                        if f['urgent']: score += 10000
                        if p['corridor_actuel'] == f['fonction_support']: score += 2000
                        score -= t_approche * 5
                        
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
                        t_app, t_mis = 15, 15
                        
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
                n_matin += 1
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
        
        # ─── SECTION 2 : VOYAGE DE L'APRÈS-MIDI (DÉBUTE À 13:45) ───
        n_am = 1
        solution_am_valide = False
        postes_am_retenus = []
        
        while not solution_am_valide and n_am <= n_matin:
            tentative_postes_am = []
            for i in range(n_am):
                tentative_postes_am.append({
                    'id': f"{t_veh}_S2_{i+1:02d}",
                    'type_veh': t_veh,
                    'veh_id': f"{t_veh}_VEH{i+1:02d}",
                    'temps_courant': 825,
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
                    if p['temps_courant'] >= 1275:
                        continue
                        
                    if not p['pause_prise'] and p['temps_courant'] >= 1000:
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
                            t_approche, t_mission = 15, 15
                            
                        heure_livraison = p['temps_courant'] + t_approche + t_mission + 10
                        
                        if heure_livraison > f['h_liv_max']:
                            continue
                            
                        score = 0
                        if f['urgent']: score += 10000
                        if p['corridor_actuel'] == f['fonction_support']: score += 2000
                        score -= t_approche * 5
                        
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
                        t_app, t_mis = 15, 15
                        
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

    flat_etapes = []
    for p in postes_finaux:
        for e in p['etapes']:
            flat_etapes.append({
                'Jour': jour,
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
            
    df_tournees = pd.DataFrame(flat_etapes)
    df_non_servis = pd.DataFrame(flux_non_servis)
    
    if not df_non_servis.empty:
        df_non_servis['Pourquoi ce flux nest pas planifie'] = "Plafond après-midi atteint"
        df_non_servis['CONTRAINTE BLOQUANTE'] = "Augmenter la capacité du matin"

    solution_globale = {
        jour: {
            'tournes': df_tournees,
            'non_servis': df_non_servis,
            'flotte_nb': suivi_flotte_max
        }
    }
    
    return solution_globale
