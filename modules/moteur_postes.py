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
        try: 
            progress_cb(0.1, f"Analyse et alignement des flux du {nom_jour}...")
        except: 
            pass

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
        ori = row[col_map['origine']] if 'origine' in col_map else row.get('Origine', row.get('Site Expéditeur', 'HSJ'))
        dest = row[col_map['destination']] if 'destination' in col_map else row.get('Destination', row.get('Site Destinataire', 'HSJ'))
        f_support = row[col_map['fonction_support']] if 'fonction_support' in col_map else row.get('Fonction support', 'GENERAL')
        cont = row[col_map['contenant']] if 'contenant' in col_map else row.get('Contenant', '')
        
        qte = 1
        for q_col in [f'Quantité {nom_jour}', f'Quantite {nom_jour}', 'Quantité', 'Quantite', 'Volume']:
            if q_col in df_flux.columns and not pd.isna(row[q_col]):
                try:
                    qte = int(row[q_col])
                    break
                except:
                    pass

        h_dep_min = str_to_min(row.get('Heure de mise à disposition min départ', '06:00:00'))
        h_liv_max = str_to_min(row.get('Heure max de livraison à la destination', '21:00:00'))
        
        is_urgent = False
        if 'urgent' in col_map:
            is_urgent = str(row[col_map['urgent']]).upper() in ['OUI', 'O', 'TRUE', '1']
        
        t_veh_req = row[col_map['type_veh']] if 'type_veh' in col_map else row.get('Type véh.', 'FOURGON')

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

    flux_matin = [f for f in flux_list if f['h_dep_min'] < 825]
    flux_apres_midi = [f for f in flux_list if f['h_dep_min'] >= 825]
    
    if isinstance(df_vehicules, pd.DataFrame):
        types_vehicules = df_vehicules['Types'].unique() if 'Types' in df_vehicules.columns else df_vehicules.index.unique()
    else:
        types_vehicules = ['FOURGON', 'HAILLON']

    postes_finaux = []
    flux_non_servis = []
    suivi_flotte_max = {}

    total_types = len(types_vehicules)
    for idx_t, t_veh in enumerate(types_vehicules):
        if progress_cb:
            try: 
                progress_cb(0.2 + 0.7 * (idx_t / total_types), f"Calcul du pavage pour {t_veh}...")
            except: 
                pass

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
                            t_mission = float(matrice_dure
