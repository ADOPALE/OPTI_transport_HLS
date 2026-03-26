import pandas as pd
import streamlit as st

def convertir_temps_manutention(valeur):
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
    """Cherche une colonne dans un DF ou une série via des mots-clés."""
    cols = df.columns if hasattr(df, 'columns') else df.index
    for c in cols:
        if any(m.lower() in str(c).lower() for m in mots_cles):
            return c
    return None

def preparer_missions_unifiees(df_flux):
    df_flux.columns = [str(c).strip() for c in df_flux.columns]
    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    missions_par_jour = {f"Quantité {j}": [] for j in jours_nom}

    # Positions fixes basées sur ton DEBUG qui a marché (16 missions)
    idx_dep = 0
    idx_dest = 1
    idx_cont = 4
    # On cherche l'index de la colonne "Nature du flux" qui contient "Volume"
    idx_nature = next((i for i, c in enumerate(df_flux.columns) if "obligation" in c.lower() or "nature du flux" in c.lower()), 10)

    for idx, row in df_flux.iterrows():
        dep = str(row.iloc[idx_dep]).strip().upper()
        dest = str(row.iloc[idx_dest]).strip().upper()
        nature_val = str(row.iloc[idx_nature]).strip().lower()

        # On ne prend que les lignes "Volume"
        if "volume" not in nature_val or dep == "" or "nan" in dep.lower():
            continue

        for jour in jours_nom:
            # On cherche la colonne qui contient le nom du jour (ex: "Quantité Lundi")
            col_j = next((c for c in df_flux.columns if jour.lower() in c.lower() and ("quantité" in c.lower() or "qte" in c.lower())), None)
            
            if col_j:
                qte = pd.to_numeric(row[col_j], errors='coerce')
                if qte > 0:
                    missions_par_jour[f"Quantité {jour}"].append({
                        "id_flux": idx,
                        "origine": dep,
                        "destination": dest,
                        "contenant": str(row.iloc[idx_cont]).strip().upper(),
                        "quantite_totale": int(qte),
                        "est_plein": "PLEIN" in str(row.iloc[5]).upper(),
                        "est_propre": "PROPRE" in str(row.iloc[6]).upper(),
                        "fenetre_end": 1200,
                        "tag_compatibilite": "MIXTE_OK" if "OUI" in str(row.iloc[8]).upper() else f"DEDIE_{idx}"
                    })
    return missions_par_jour

def calculer_capacite_emport_finale(mission, vehicule_row, df_contenants):
    nom_cont = mission['contenant']
    col_libelle = trouver_colonne(df_contenants, ["libellé", "contenant"])
    row_cont = df_contenants[df_contenants[col_libelle].str.upper() == nom_cont]
    
    if row_cont.empty: return 2 # Valeur par défaut si inconnu (ex: VL)

    c = row_cont.iloc[0]
    # On cherche les colonnes de dimensions dans le véhicule
    col_L_v = trouver_colonne(pd.DataFrame(columns=vehicule_row.index), ["longueur interne"])
    col_l_v = trouver_colonne(pd.DataFrame(columns=vehicule_row.index), ["largeur interne"])
    
    L_v = float(vehicule_row[col_L_v]) if col_L_v else 4.0
    l_v = float(vehicule_row[col_l_v]) if col_l_v else 2.0
    
    L_c = float(c.get(trouver_colonne(df_contenants, ["longueur (m)"]), 1.2))
    l_c = float(c.get(trouver_colonne(df_contenants, ["largeur (m)"]), 0.8))

    capa = max((L_v // L_c) * (l_v // l_c), (L_v // l_c) * (l_v // L_c))
    return max(1, int(capa))

def simuler_tournees_quotidiennes(missions_du_jour, df_vehicules, df_contenants, matrice_duree):
    if not missions_du_jour: return []
    
    col_quai = trouver_colonne(df_vehicules, ["mise à quai", "manœuvre"])
    col_manut = trouver_colonne(df_vehicules, ["Manutention", "sans quai"])

    rotations = []
    for m in missions_du_jour:
        # On utilise le premier véhicule pour la simulation par défaut
        v_row = df_vehicules.iloc[0]
        capa = calculer_capacite_emport_finale(m, v_row, df_contenants)
        
        qte_restante = m['quantite_totale']
        while qte_restante > 0:
            emport = min(qte_restante, capa)
            
            t_quai = convertir_temps_manutention(v_row[col_quai])
            t_manut = convertir_temps_manutention(v_row[col_manut])
            
            try:
                trajet = matrice_duree.loc[m['origine'], m['destination']] * 2
            except:
                trajet = 40.0
                
            duree_rot = (t_quai * 2) + (t_manut * emport * 2) + trajet
            rotations.append({"duree": duree_rot, "fin": m['fenetre_end']})
            qte_restante -= emport

    # Lissage RH (Postes de 450 min)
    postes, cumul = [], 0
    for r in sorted(rotations, key=lambda x: x['fin']):
        if cumul + r['duree'] <= 450:
            cumul += r['duree']
        else:
            postes.append(cumul)
            cumul = r['duree']
    if cumul > 0: postes.append(cumul)
    
    return postes
