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

def optimiser_postes_jour(df_flux, df_vehicules, df_contenants=None, df_sites=None, 
                          matrice_duree=None, matrice_dist=None, params_log=None, 
                          nom_jour="Lundi", autoriser_tournees=True, budget_s=10, progress_cb=None):
    """
    MOTEUR TRANSPORT CONFORME À LA SIGNATURE COMPLÈTE DE APP.PY (Ligne 290)
    - Reçoit et gère l'ensemble des arguments nommés (nom_jour, budget_s, progress_cb, etc.)
    - Ouverture stricte à 06:00 pour le Matin (S1)
    - Pause obligatoire au dépôt insérée automatiquement
    - Plafond strict l'après-midi (S2 <= S1)
    """
    
    # Notification de progression si un callback est fourni
    if progress_cb:
        try:
            progress_cb(0.1, f"Initialisation de la journée {nom_jour}...")
        except:
            pass

    jour = nom_jour
    flux_list = []
    
    # Extraction et formatage des flux
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

    # Séparation temporelle Matin (S1) / Après-midi (S2) à 13h45 (825 min)
    flux_matin = [f for f in flux_list if f['h_dep_min'] < 825]
    flux_apres_midi = [f for f in flux_list if f['h_dep_min'] >= 825]
    
    types_vehicules = df_vehicules['Types'].unique() if 'Types' in df_vehicules.columns else df_vehicules.index.unique()
    
    postes_finaux = []
    flux_non_servis = []
    suivi_flotte_max = {}

    total_types = len(types_vehicules)
    for idx_t, t_veh in enumerate(types_vehicules):
        if progress_cb:
            try:
                progress_cb(0.2 + 0.7 * (idx_t / total_types), f"Traitement des véhicules {t_veh}...")
            except:
                pass

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
                    'temps_courant': 360, # 06h00 strict
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
                    if p['temps_courant'] >= 810: # 13h30
                        continue
                        
                    # Pause réglementaire milieu de poste
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
        # Interdiction absolue de dépasser n_matin (Plafond de flotte)
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
                    'temps_courant': 825, # 13h45 strict
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
                    if p['temps_courant'] >= 1275: # 21h15
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
                    p
