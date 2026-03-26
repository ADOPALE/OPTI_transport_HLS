import pandas as pd
import streamlit as st

def convertir_temps_manutention(valeur):
    if isinstance(valeur, pd.Timestamp) or hasattr(valeur, 'hour'):
        return valeur.hour * 60 + valeur.minute + valeur.second / 60
    try:
        val_str = str(valeur)
        if ":" in val_str:
            parts = list(map(int, val_str.split(':')))
            return parts[0] * 60 + parts[1] + (parts[2]/60 if len(parts)>2 else 0)
        return float(valeur)
    except:
        return 0.0

def trouver_colonne(df, mots_cles):
    cols = df.columns if hasattr(df, 'columns') else df.index
    for c in cols:
        if any(m.lower() in str(c).lower() for m in mots_cles):
            return c
    return None

def preparer_missions_unifiees(df_flux):
    # On définit les index fixes basés sur ton image
    IDX_DEP = 0
    IDX_DEST = 1
    IDX_CONT = 4
    # La colonne "Volume" est juste avant les couleurs vertes (index 10)
    IDX_NATURE = 10 
    # Les colonnes Quantités commencent à l'index 11 (Lundi) jusqu'à 17 (Dimanche)
    IDX_JOURS_START = 11

    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    missions_par_jour = {f"Quantité {j}": [] for j in jours_nom}

    for idx, row in df_flux.iterrows():
        # Lecture par position ILOC
        dep = str(row.iloc[IDX_DEP]).strip().upper()
        dest = str(row.iloc[IDX_DEST]).strip().upper()
        nature = str(row.iloc[IDX_NATURE]).strip().lower()

        # Si la ligne est vide ou n'est pas un "Volume", on passe
        if dep == "" or "nan" in dep.lower() or "volume" not in nature:
            continue

        for i, jour in enumerate(jours_nom):
            # On va chercher la cellule à l'index 11, 12, 13...
            val_qte = row.iloc[IDX_JOURS_START + i]
            qte = pd.to_numeric(val_qte, errors='coerce')
            
            if pd.notnull(qte) and qte > 0:
                missions_par_jour[f"Quantité {jour}"].append({
                    "id_flux": idx,
                    "origine": dep,
                    "destination": dest,
                    "contenant": str(row.iloc[IDX_CONT]).strip().upper(),
                    "quantite_totale": int(qte),
                    "fenetre_end": 1200,
                    "tag_compatibilite": "MIXTE_OK"
                })
    return missions_par_jour

def calculer_capacite_emport_finale(mission, vehicule_row, df_contenants):
    # On simplifie : si c'est un camion, on met une capacité de 8, si c'est un VL on met 2
    # Cela permet de vérifier si le problème vient du calcul complexe
    v_name = str(vehicule_row.iloc[0]).upper()
    if "VL" in v_name or "FOURGON" in v_name:
        return 2
    return 8 

def simuler_tournees_quotidiennes(missions_du_jour, df_vehicules, df_contenants, matrice_duree):
    if not missions_du_jour:
        return []

    # On récupère les colonnes de temps du premier véhicule
    v_row = df_vehicules.iloc[0]
    col_quai = trouver_colonne(df_vehicules, ["mise à quai", "manœuvre"])
    col_manut = trouver_colonne(df_vehicules, ["Manutention", "sans quai"])
    
    t_quai = convertir_temps_manutention(v_row[col_quai]) if col_quai else 10.0
    t_manut = convertir_temps_manutention(v_row[col_manut]) if col_manut else 1.0

    rotations = []
    for m in missions_du_jour:
        capa = calculer_capacite_emport_finale(m, v_row, df_contenants)
        
        qte_restante = m['quantite_totale']
        while qte_restante > 0:
            emport = min(qte_restante, capa)
            
            try:
                trajet = matrice_duree.loc[m['origine'], m['destination']] * 2
            except:
                trajet = 30.0 # Valeur de secours
            
            duree_rot = (t_quai * 2) + (t_manut * emport * 2) + trajet
            rotations.append({"duree": duree_rot, "fin": 1200})
            qte_restante -= emport

    # Lissage : 450 min par chauffeur
    postes, cumul = [], 0
    for r in rotations:
        if cumul + r['duree'] <= 450:
            cumul += r['duree']
        else:
            postes.append(cumul)
            cumul = r['duree']
    if cumul > 0: postes.append(cumul)
    
    return postes
