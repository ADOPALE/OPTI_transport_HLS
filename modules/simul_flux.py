import pandas as pd
import streamlit as st
import math

def convertir_temps_manutention(valeur):
    """
    Convertit une valeur Excel (datetime.time, str ou float) en minutes.
    Gère les formats HH:MM:SS des référentiels véhicules.
    """
    if isinstance(valeur, pd.Timestamp) or hasattr(valeur, 'hour'):
        return valeur.hour * 60 + valeur.minute + valeur.second / 60
    elif isinstance(valeur, str):
        try:
            parts = list(map(int, valeur.split(':')))
            if len(parts) == 3: # HH:MM:SS
                return parts[0] * 60 + parts[1] + parts[2] / 60
            elif len(parts) == 2: # MM:SS
                return parts[0] + parts[1] / 60
        except:
            return 0.0
    return float(valeur) if pd.notnull(valeur) else 0.0

def preparer_missions_unifiees(df_flux):
    # 1. Nettoyage des colonnes
    df_flux.columns = [str(c).strip() for c in df_flux.columns]
    
    # 2. Identification ultra-souple des colonnes (par mots-clés)
    def trouver_col(mots):
        for c in df_flux.columns:
            if any(m.lower() in c.lower() for m in mots):
                return c
        return None

    col_depart = trouver_col(["Point de départ"])
    col_dest = trouver_col(["Point de destination"])
    col_contenant = trouver_col(["Nature de contenant"])
    # On cherche juste "obligation" ou "nature du flux" pour cette colonne très longue
    col_nature_flux = trouver_col(["obligation", "nature du flux"]) 
    
    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    # On cherche "Quantité Lundi" ou juste "Lundi"
    jours_map = {j: trouver_col([f"Quantité {j}", j]) for j in jours_nom}

    missions_par_jour = {f"Quantité {j}": [] for j in jours_nom}

    # 3. Parcours des lignes
    for idx, row in df_flux.iterrows():
        # A. On ignore les lignes où le départ ou la destination sont vides
        if pd.isna(row.get(col_depart)) or str(row.get(col_depart)).strip() == "":
            continue
            
        # B. On vérifie la nature du flux (Doit contenir "Volume")
        nature_val = str(row.get(col_nature_flux, "")).strip().capitalize()
        if "Volume" not in nature_val:
            # Optionnel : décommenter pour débugger dans la console
            # print(f"Ligne {idx} rejetée : Nature = {nature_val}")
            continue

        # C. Extraction des horaires (avec sécurité)
        # Sur ton image, les colonnes Heure de mise à dispo sont vers la fin
        col_h_dispo = trouver_col(["Heure de mise à disposition"])
        col_h_limite = trouver_col(["Heure max de livraison"])
        
        try:
            h_start = row[col_h_dispo].hour * 60 + row[col_h_dispo].minute
            h_end = row[col_h_limite].hour * 60 + row[col_h_limite].minute
        except:
            h_start, h_end = 360, 1200 # 6h-20h par défaut

        # D. Ventilation par jour
        for jour_nom, col_excel in jours_map.items():
            if col_excel:
                val_qte = row[col_excel]
                qte = pd.to_numeric(val_qte, errors='coerce')
                
                if pd.notnull(qte) and qte > 0:
                    missions_par_jour[f"Quantité {jour_nom}"].append({
                        "id_flux": idx,
                        "origine": str(row[col_depart]).strip().upper(),
                        "destination": str(row[col_dest]).strip().upper(),
                        "contenant": str(row[col_contenant]).strip().upper(),
                        "quantite_totale": int(qte),
                        "est_plein": "PLEIN" in str(row.get("Plein / vide", "Plein")).upper(),
                        "est_propre": "PROPRE" in str(row.get("Sale / propre", "Propre")).upper(),
                        "fenetre_start": h_start,
                        "fenetre_end": h_end,
                        "tag_compatibilite": "MIXTE_OK" if "OUI" in str(row.get("Transport mixte possible", "")).upper() else f"DEDIE_{idx}",
                        "exclusions": []
                    })

    return missions_par_jour

def calculer_capacite_emport_finale(mission, vehicule_name, df_vehicules, df_contenants):
    """Calcule la capacité réelle (Tetris + Poids)."""
    config = st.session_state.get("params_logistique", {})
    taux = config.get("securite_remplissage", 0.85)

    spec_v = df_vehicules[df_vehicules['Types'] == vehicule_name].iloc[0]
    
    # Vérification de compatibilité technique (OUI/NON dans l'Excel)
    if spec_v.get(mission['contenant'], "NON") == "NON":
        return 0

    spec_c = df_contenants[df_contenants['libellé'] == mission['contenant']].iloc[0]
    
    L_cam, l_cam = spec_v['dim longueur interne (m)'], spec_v['dim largeur interne (m)']
    dim1, dim2 = spec_c['dim longueur (m)'], spec_c['dim largeur (m)']

    # Calcul Tetris avec rotation
    capa_A = (L_cam // dim1) * (l_cam // dim2)
    capa_B = (L_cam // dim2) * (l_cam // dim1)
    meilleur_sol = max(capa_A, capa_B)

    # Vérification du poids max
    poids_u = spec_c['Poids plein (kg)'] if mission['est_plein'] else spec_c['Poids vide (kg)']
    try:
        cu_kg = float(str(spec_v['Poids max chargement']).upper().replace('T', '').replace(',', '.').strip()) * 1000
    except:
        cu_kg = 5000.0
    
    capa_poids = int(cu_kg // poids_u) if poids_u > 0 else meilleur_sol

    return int(min(meilleur_sol, capa_poids) * taux)

def calculer_duree_rotation(mission, vehicule_name, qte, df_vehicules, matrice_duree):
    """Calcule le cycle complet en minutes."""
    spec_v = df_vehicules[df_vehicules['Types'] == vehicule_name].iloc[0]
    
    t_quai_min = convertir_temps_manutention(spec_v['Temps de mise à quai - manœuvre, contact/admin min (minutes)'])
    t_unit_sec = convertir_temps_manutention(spec_v['Manutention on sans quai (minutes / contenants)'])
    
    # Manutention totale (Aller + Retour)
    manut_min = (t_quai_min * 2) + (t_unit_sec * qte * 2)
    
    try:
        trajet = matrice_duree.loc[mission['origine'], mission['destination']] * 2
    except:
        trajet = 40.0 # Valeur par défaut
        
    return manut_min + trajet

def simuler_tournees_quotidiennes(missions_du_jour, df_vehicules, df_contenants, matrice_duree):
    """Moteur de groupage et assignation RH."""
    if not missions_du_jour:
        return []

    config = st.session_state.get("params_logistique", {"rh": {"amplitude_totale": 450}})
    v_selectionnes = config.get("vehicules_selectionnes", df_vehicules['Types'].tolist())
    
    rotations_liste = []

    for m in missions_du_jour:
        meilleure_capa, meilleur_v = 0, None
        for v_name in v_selectionnes:
            capa = calculer_capacite_emport_finale(m, v_name, df_vehicules, df_contenants)
            if capa > meilleure_capa:
                meilleure_capa, meilleur_v = capa, v_name
        
        if meilleure_capa <= 0: continue

        qte_restante = m['quantite_totale']
        while qte_restante > 0:
            emport = min(qte_restante, meilleure_capa)
            duree = calculer_duree_rotation(m, meilleur_v, emport, df_vehicules, matrice_duree)
            rotations_liste.append({"duree": duree, "fin": m['fenetre_end']})
            qte_restante -= emport

    # Lissage RH (7h30 par chauffeur)
    rotations_liste.sort(key=lambda x: x['fin'])
    postes = []
    current_poste_duree = 0
    amp_max = config.get("rh", {}).get("amplitude_totale", 450)

    for rot in rotations_liste:
        if current_poste_duree + rot["duree"] <= amp_max:
            current_poste_duree += rot["duree"]
        else:
            postes.append(current_poste_duree)
            current_poste_duree = rot["duree"]
    if current_poste_duree > 0: postes.append(current_poste_duree)

    return postes
