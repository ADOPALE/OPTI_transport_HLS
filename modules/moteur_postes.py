import pandas as pd
import datetime as dt
import math

def str_to_min(time_str):
    """Convertit 'HH:MM:SS' ou 'HH:MM' en minutes depuis minuit."""
    if pd.isna(time_str) or not isinstance(time_str, str):
        return 0
    parts = list(map(int, time_str.split(':')))
    if len(parts) == 3:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return 0

def min_to_str(minutes):
    """Convertit des minutes en format 'HH:MM'."""
    h = int(minutes // 60) % 24
    m = int(minutes % 60)
    return f"{h:02d}:{m:02d}"

def optimiser_journee_transport(df_flux, df_vehicules, df_rh, matrice_duree, matrice_dist):
    """
    Moteur de planification respectant :
    - Ouverture à 06h00 de N postes
    - Pause obligatoire au milieu du poste +/- 60 min
    - Spécialisation par corridor / fonction support
    - Priorité absolue aux urgences
    - Itération N + 1 si rupture de SLA (livraison hors créneau)
    """
    
    # 1. Préparation et tri initial des flux du jour
    flux_list = []
    for idx, row in df_flux.iterrows():
        flux_list.append({
            'id': row.get('Flux', idx),
            'origine': row['Origine'],
            'destination': row['Destination'],
            'contenant': row['Contenant'],
            'quantite': int(row['Quantité']),
            'fonction_support': row['Fonction support'],
            'h_dep_min': str_to_min(row['Heure de mise à disposition min départ']),
            'h_liv_max': str_to_min(row['Heure max de livraison à la destination']),
            'urgent': str(row.get('Urgence', 'NON')).upper() == 'OUI',
            'type_veh_requis': row['Type véh.'] # Assumé pré-calculé dans prep_transport
        })

    # Séparation Matin (fenêtre de livraison max avant 13h45) / Après-midi
    flux_matin = [f for f in flux_list if f['h_dep_min'] < 825] # 13h45 = 825 min
    flux_apres_midi = [f for f in flux_list if f['h_dep_min'] >= 825]
    
    types_vehicules = df_vehicules['Types'].unique()
    
    resultats_postes = []
    flux_non_servis = []
    flotte_finale = {}

    for t_veh in types_vehicules:
        flux_t_matin = [f for f in flux_matin if f['type_veh_requis'] == t_veh]
        flux_t_am = [f for f in flux_apres_midi if f['type_veh_requis'] == t_veh]
        
        if not flux_t_matin and not flux_t_am:
            continue
            
        # --- PLANIFICATION DU MATIN (S1 : 06h00 - 13h30) ---
        # On commence à N = 1 poste matin
        n_matin = 1
        solution_matin_valide = False
        postes_matin_retenus = []
        
        while not solution_matin_valide and n_matin <= 50: # Borne de sauvegarde
            tentative_postes = []
            for i in range(n_matin):
                tentative_postes.append({
                    'id': f"{t_veh}_S1_{i+1:02d}",
                    'type_veh': t_veh,
                    'veh_id': f"{t_veh}_VEH{i+1:02d}",
                    'temps_courant': 360, # 06h00 = 360 min
                    'position': 'HSJ', # Dépôt initial par défaut
                    'corridor_actuel': None,
                    'pause_prise': False,
                    'etapes': []
                })
            
            # Initialisation des étapes (Prise de poste)
            for p in tentative_postes:
                p['etapes'].append({'heure_deb': 360, 'heure_fin': 380, 'type': 'Prise de poste', 'site': p['position']})
                p['temps_courant'] = 380
                
            flux_restants = list(flux_t_matin)
            echec_sla = False
            
            while flux_restants:
                # Trouver la meilleure affectation (Poste, Flux)
                meilleur_score = -1e9
                cible_poste = None
                cible_flux = None
                
                for p in tentative_postes:
                    # Vérifier si le poste est arrivé en fin de vacation (13h30 = 810 min)
                    if p['temps_courant'] >= 810:
                        continue
                        
                    # Gestion de la pause obligatoire au milieu du poste (Autour de 9h30 / 10h00)
                    # Milieu du poste = 360 + 225 = 585 min (09h45). Fenêtre +/- 60min : 525 à 645
                    if not p['pause_prise'] and p['temps_courant'] >= 540: # Déclencher dès 9h00 si possible
                        p['etapes'].append({'heure_deb': p['temps_courant'], 'heure_fin': p['temps_courant'] + 20, 'type': 'Pause (dépôt)', 'site': 'HSJ'})
                        p['temps_courant'] += 20
                        p['position'] = 'HSJ'
                        p['pause_prise'] = True
                        continue

                    for f in flux_restants:
                        # Calculer les temps de transit
                        temps_approche = float(matrice_duree.at[p['position'], f['origine']])
                        temps_mission = float(matrice_duree.at[f['origine'], f['destination']])
                        temps_operations = 10 # Temps fixe de chargement/déchargement estimé
                        
                        heure_arrivee_dest = p['temps_courant'] + temps_approche + temps_mission + temps_operations
                        
                        # Contrainte de temps : Est-ce qu'on livre après la borne max du flux ?
                        if heure_arrivee_dest > f['h_liv_max']:
                            continue # Ce poste ne peut pas prendre ce flux sans briser le SLA
                            
                        # Score de l'Heuristique
                        # 1. Priorité absolue à l'urgence
                        score = 5000 if f['urgent'] else 0
                        # 2. Synergie de Corridor (Même fonction support)
                        if p['corridor_actuel'] == f['fonction_support']:
                            score += 1000
                        # 3. Minimisation du temps à vide (approche)
                        score -= temps_approche * 2
                        
                        if score > meilleur_score:
                            meilleur_score = score
                            cible_poste = p
                            cible_flux = f
                
                if cible_poste and cible_flux:
                    # Assigner le flux au poste cible
                    f = cible_flux
                    p = cible_poste
                    
                    temps_approche = float(matrice_duree.at[p['position'], f['origine']])
                    dist_approche = float(matrice_dist.at[p['position'], f['origine']])
                    temps_mission = float(matrice_duree.at[f['origine'], f['destination']])
                    dist_mission = float(matrice_dist.at[f['origine'], f['destination']])
                    
                    # Étape approche
                    p['etapes'].append({
                        'heure_deb': p['temps_courant'], 'heure_fin': p['temps_courant'] + temps_approche,
                        'type': 'Approche à vide', 'site': f['origine'], 'distance': dist_approche
                    })
                    p['temps_courant'] += temps_approche
                    
                    # Étape mission chargée
                    p['etapes'].append({
                        'heure_deb': p['temps_courant'], 'heure_fin': p['temps_courant'] + temps_mission + 10,
                        'type': 'Mission (chargé)', 'site': f['destination'], 'distance': dist_mission, 'flux': f['id']
                    })
                    p['temps_courant'] += temps_mission + 10
                    p['position'] = f['destination']
                    p['corridor_actuel'] = f['fonction_support']
                    
                    flux_restants.remove(f)
                else:
                    # S'il reste des flux mais qu'aucun poste ne peut les prendre à temps : Échec de la configuration N
                    echec_sla = True
                    break
            
            if echec_sla:
                n_matin += 1 # On incrémente le nombre de postes nécessaires le matin
            else:
                # Clôturer les postes du matin conformes
                for p in tentative_postes:
                    if not p['pause_prise']: # Forcer la pause si non prise
                        p['etapes'].append({'heure_deb': p['temps_courant'], 'heure_fin': p['temps_courant'] + 20, 'type': 'Pause', 'site': p['position']})
                        p['temps_courant'] += 20
                    p['etapes'].append({'heure_deb': p['temps_courant'], 'heure_fin': 810, 'type': 'Clôture poste', 'site': p['position']})
                    p['temps_courant'] = 810
                postes_matin_retenus = tentative_postes
                solution_matin_valide = True

        # Enregistrement de la flotte nécessaire (Max du matin)
        flotte_finale[t_veh] = n_matin
        resultats_postes.extend(postes_matin_retenus)
        
        # --- PLANIFICATION DE L'APRÈS-MIDI (S2 : 13h45 - 21h15) ---
        # On commence à N = 1 poste mais limité par le plafond N_matin
        n_apres_midi = 1
        solution_am_valide = False
        postes_am_retenus = []
        
        while not solution_am_valide and n_apres_midi <= n_matin:
            tentative_postes_am = []
            for i in range(n_apres_midi):
                tentative_postes_am.append({
                    'id': f"{t_veh}_S2_{i+1:02d}",
                    'type_veh': t_veh,
                    'veh_id': f"{t_veh}_VEH{i+1:02d}", # Réutilisation des mêmes camions
                    'temps_courant': 825, # 13h45 = 825 min
                    'position': 'HSJ',
                    'corridor_actuel': None,
                    'pause_prise': False,
                    'etapes': []
                })
                
            for p in tentative_postes_am:
                p['etapes'].append({'heure_deb': 825, 'heure_fin': 845, 'type': 'Prise de poste', 'site': p['position']})
                p['temps_courant'] = 845
                
            flux_restants = list(flux_t_am)
            echec_sla_am = False
            
            while flux_restants:
                meilleur_score = -1e9
                cible_poste = None
                cible_flux = None
                
                for p in tentative_postes_am:
                    if p['temps_courant'] >= 1275: # 21h15 = 1275 min
                        continue
                    
                    # Pause au milieu du poste d'après-midi (Autour de 17h30 = 1050 min)
                    if not p['pause_prise'] and p['temps_courant'] >= 1000:
                        p['etapes'].append({'heure_deb': p['temps_courant'], 'heure_fin': p['temps_courant'] + 20, 'type': 'Pause (dépôt)', 'site': 'HSJ'})
                        p['temps_courant'] += 20
                        p['position'] = 'HSJ'
                        p['pause_prise'] = True
                        continue

                    for f in flux_restants:
                        temps_approche = float(matrice_duree.at[p['position'], f['origine']])
                        temps_mission = float(matrice_duree.at[f['origine'], f['destination']])
                        heure_arrivee_dest = p['temps_courant'] + temps_approche + temps_mission + 10
                        
                        if heure_arrivee_dest > f['h_liv_max']:
                            continue
                            
                        score = 5000 if f['urgent'] else 0
                        if p['corridor_actuel'] == f['fonction_support']:
                            score += 1000
                        score -= temps_approche * 2
                        
                        if score > meilleur_score:
                            meilleur_score = score
                            cible_poste = p
                            cible_flux = f
                            
                if cible_poste and cible_flux:
                    f = cible_flux
                    p = cible_poste
                    temps_approche = float(matrice_duree.at[p['position'], f['origine']])
                    dist_approche = float(matrice_dist.at[p['position'], f['origine']])
                    temps_mission = float(matrice_duree.at[f['origine'], f['destination']])
                    dist_mission = float(matrice_dist.at[f['origine'], f['destination']])
                    
                    p['etapes'].append({
                        'heure_deb': p['temps_courant'], 'heure_fin': p['temps_courant'] + temps_approche,
                        'type': 'Approche à vide', 'site': f['origine'], 'distance': dist_approche
                    })
                    p['temps_courant'] += temps_approche
                    
                    p['etapes'].append({
                        'heure_deb': p['temps_courant'], 'heure_fin': p['temps_courant'] + temps_mission + 10,
                        'type': 'Mission (chargé)', 'site': f['destination'], 'distance': dist_mission, 'flux': f['id']
                    })
                    p['temps_courant'] += temps_mission + 10
                    p['position'] = f['destination']
                    p['corridor_actuel'] = f['fonction_support']
                    
                    flux_restants.remove(f)
                else:
                    echec_sla_am = True
                    break
            
            if echec_sla_am:
                if n_apres_midi < n_matin:
                    n_apres_midi += 1
                else:
                    # Plafond atteint ! Les flux restants sont irrécupérables sans dépasser la flotte du matin
                    flux_non_servis.extend(flux_restants)
                    break
            else:
                for p in tentative_postes_am:
                    p['etapes'].append({'heure_deb': p['temps_courant'], 'heure_fin': 1275, 'type': 'Clôture poste', 'site': p['position']})
                    p['temps_courant'] = 1275
                postes_am_retenus = tentative_postes_am
                solution_am_valide = True
                
        resultats_postes.extend(postes_am_retenus)

    return resultats_postes, flux_non_servis, flotte_finale
