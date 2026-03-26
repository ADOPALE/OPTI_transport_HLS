import pandas as pd
import streamlit as st
import math

def convertir_temps_manutention(valeur):
    """Gère les formats HH:MM:SS ou numériques d'Excel."""
    if isinstance(valeur, pd.Timestamp) or hasattr(valeur, 'hour'):
        return valeur.hour * 60 + valeur.minute + valeur.second / 60
    try:
        if isinstance(valeur, str) and ":" in valeur:
            parts = list(map(int, valeur.split(':')))
            return parts[0] * 60 + parts[1] + (parts[2]/60 if len(parts)>2 else 0)
        return float(valeur)
    except:
        return 0.0

def trouver_colonne(df, mots_cles):
    """Cherche une colonne dans un DF via des mots-clés."""
    for c in df.columns:
        if any(m.lower() in str(c).lower() for m in mots_cles):
            return c
    return None

def preparer_missions_unifiees(df_flux):
    df_flux.columns = [str(c).strip() for c in df_flux.columns]
    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    missions_par_jour = {f"Quantité {j}": [] for j in jours_nom}

    # Détection des colonnes clés par position ou mot-clé
    idx_dep = 0
    idx_dest = 1
    idx_cont = 4
    col_nature = trouver_colonne(df_flux, ["obligation", "nature du flux"])

    for idx, row in df_flux.iterrows():
        dep = str(row.iloc[idx_dep]).strip().upper()
        dest = str(row.iloc[idx_dest]).strip().upper()
        nature_val = str(row.get(col_nature, "")).strip().lower()

        if "volume" not in nature_val or dep == "" or "nan" in dep.lower():
            continue

        for jour in jours_nom:
            col_j = trouver_colonne(df_flux, [f"Quantité {jour}", jour])
            if col_j:
                qte = pd.to_numeric(row[col_j], errors='coerce')
                if qte > 0:
                    missions_par_jour[f"Quantité {jour}"].append({
                        "id_flux": idx,
                        "origine": dep,
                        "destination": dest,
                        "contenant": str(row.iloc[idx_cont]).strip().upper(),
                        "quantite_totale": int(qte),
                        "fenetre_end": 1200 # 20h00
                    })
    return missions_par_jour

def calculer_capacite_emport_finale(mission, vehicule_row, df_contenants):
    """Calcule combien de contenants rentrent dans ce véhicule spécifique."""
    nom_cont = mission['contenant']
    # Chercher la ligne du contenant
    col_libelle = trouver_colonne(df_contenants, ["libellé", "contenant"])
    row_cont = df_contenants[df_contenants[col_libelle].str.upper() == nom_cont]
    
    if row_cont.empty: return 1 # Par sécurité

    c = row_cont.iloc[0]
    # Dimensions Camion (mots clés pour éviter le KeyError)
    L_v = vehicule_row.get(trouver_colonne(pd.DataFrame(columns=vehicule_row.index), ["longueur interne"]), 4.0)
    l_v = vehicule_row.get(trouver_colonne(pd.DataFrame(columns=vehicule_row.index), ["largeur interne"]), 2.0)
    
    # Dimensions Contenant
    L_c = c.get(trouver_colonne(df_contenants, ["longueur (m)"]), 1.2)
    l_c = c.get(trouver_colonne(df_contenants, ["largeur (m)"]), 0.8)

    capa = max((L_v // L_c) * (l_v // l_c), (L_v // l_c) * (l_v // L_c))
    return max(1, int(capa))

def simuler_tournees_quotidiennes(missions_du_jour, df_vehicules, df_contenants, matrice_duree):
    if not missions_du_jour: return []
    
    # Identification des colonnes de temps dans le référentiel véhicules
    col_quai = trouver_colonne(df_vehicules, ["mise à quai", "manœuvre"])
    col_manut = trouver_colonne(df_vehicules, ["Manutention", "sans quai"])
    col_type_v = trouver_colonne(df_vehicules, ["Types", "véhicule"])

    rotations = []
    for m in missions_du_jour:
        # On prend le premier véhicule dispo pour le test (souvent le plus gros)
        v_row = df_vehicules.iloc[0] 
        v_name = v_row[col_type_v]
        
        capa = calculer_capacite_emport_finale(m, v_row, df_contenants)
        
        qte_restante = m['quantite_totale']
        while qte_restante > 0:
            emport = min(qte_restante, capa)
            
            # Calcul durée
            t_quai = convertir_temps_manutention(v_row[col_quai])
            t_manut = convertir_temps_manutention(v_row[col_manut])
            
            try:
                trajet = matrice_duree.loc[m['origine'], m['destination']] * 2
            except:
                trajet = 40.0
                
            duree_rot = (t_quai * 2) + (t_manut * emport * 2) + trajet
            rotations.append({"duree": duree_rot, "fin": m['fenetre_end']})
            qte_restante -= emport

    # Lissage RH (Postes de 7h30 = 450 min)
    rotations.sort(key=lambda x: x['fin'])
    postes, cumul = [], 0
    for r in rotations:
        if cumul + r['duree'] <= 450:
            cumul += r['duree']
        else:
            postes.append(cumul)
            cumul = r['duree']
    if cumul > 0: postes.append(cumul)
    
    return postes
