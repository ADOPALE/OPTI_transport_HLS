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


def generer_planning_complet(missions_hebdo, df_vehicules, df_contenants, matrice_duree):
    """
    Organise les missions en journées de travail réelles pour chaque chauffeur.
    """
    planning_final = {}
    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    # On utilise le premier véhicule par défaut pour cette version
    # (Peut être optimisé pour choisir le véhicule selon la mission)
    v_row = df_vehicules.iloc[0]
    v_type = str(v_row.iloc[0]).upper()

    for jour in jours_nom:
        key = f"Quantité {jour}"
        missions = missions_hebdo.get(key, [])
        
        # 1. Création de la liste de toutes les rotations nécessaires
        toutes_rotations = []
        for m in missions:
            # On récupère la capacité (8 pour camion, 2 pour VL par défaut ou via calcul)
            capa = 8 if "VL" not in v_type else 2
            
            qte_restante = m['quantite_totale']
            while qte_restante > 0:
                emport = min(qte_restante, capa)
                
                # Calcul des durées détaillées
                trajet = 30.0 # Valeur par défaut
                try:
                    trajet = matrice_duree.loc[m['origine'], m['destination']]
                except: pass

                # Temps de manutention (10min fixe + 1min/contenant)
                t_manut = 10.0 + (1.0 * emport)
                duree_totale = (t_manut + trajet) * 2

                toutes_rotations.append({
                    "label": f"{m['origine']} -> {m['destination']}",
                    "contenant_type": m['contenant'],
                    "contenant_qte": emport,
                    "t_manut": t_manut * 2,
                    "t_roulage": trajet * 2,
                    "duree_totale": duree_totale,
                    "fenetre_end": m.get('fenetre_end', 1200)
                })
                qte_restante -= emport

        # 2. Assignation des rotations aux chauffeurs (Lissage 7h30)
        chauffeurs = []
        if toutes_rotations:
            # Tri par heure de fin souhaitée
            toutes_rotations.sort(key=lambda x: x['fenetre_end'])
            
            curr_c = {"type_vehicule": v_type, "tournees": [], "t_total": 0, "t_roulage": 0, "t_manut": 0}
            heure_actuelle = 360 # 06:00 du matin

            for rot in toutes_rotations:
                if curr_c["t_total"] + rot["duree_totale"] <= 450:
                    rot["debut"] = heure_actuelle + curr_c["t_total"]
                    rot["fin"] = rot["debut"] + rot["duree_totale"]
                    curr_c["tournees"].append(rot)
                    curr_c["t_total"] += rot["duree_totale"]
                    curr_c["t_roulage"] += rot["t_roulage"]
                    curr_c["t_manut"] += rot["t_manut"]
                else:
                    chauffeurs.append(curr_c)
                    curr_c = {"type_vehicule": v_type, "tournees": [], "t_total": rot["duree_totale"], 
                              "t_roulage": rot["t_roulage"], "t_manut": rot["t_manut"]}
                    rot["debut"] = heure_actuelle
                    rot["fin"] = rot["debut"] + rot["duree_totale"]
                    curr_c["tournees"].append(rot)
            
            if curr_c["tournees"]:
                chauffeurs.append(curr_c)

        planning_final[key] = {"chauffeurs": chauffeurs}

    return planning_final

# Dans modules/simul_flux.py

def generer_visuel_bin_packing(contenant_type, qte, vehicule_type, df_vehicules, df_contenants):
    import plotly.graph_objects as go
    
    # Dimensions par défaut sécurisées
    L_v, l_v = (4.0, 2.3) if "VL" not in str(vehicule_type).upper() else (3.2, 1.9)
    L_c, l_c = 1.2, 0.8 # Dimensions standards Armoire/Palette
    
    # Tentative de récupération des vraies dimensions dans le référentiel
    try:
        col_lib = trouver_colonne(df_contenants, ["libellé", "contenant"])
        row_c = df_contenants[df_contenants[col_lib].str.upper() == str(contenant_type).upper()].iloc[0]
        L_c = float(row_c[trouver_colonne(df_contenants, ["longueur"])])
        l_c = float(row_c[trouver_colonne(df_contenants, ["largeur"])])
    except:
        pass # Garde les valeurs par défaut si erreur

    fig = go.Figure()
    # Dessin du contour du camion
    fig.add_shape(type="rect", x0=0, y0=0, x1=L_v, y1=l_v, 
                  line=dict(color="RoyalBlue", width=3), fillcolor="LightSteelBlue", opacity=0.2)

    x_curr, y_curr = 0.0, 0.0
    count = 0
    
    # Boucle de placement simplifiée
    for i in range(int(qte)):
        if y_curr + l_c > l_v + 0.01: # Si dépasse la largeur, nouvelle colonne
            y_curr = 0
            x_curr += L_c
        
        if x_curr + L_c <= L_v + 0.01: # Si rentre dans la longueur
            fig.add_shape(type="rect", x0=x_curr, y0=y_curr, x1=x_curr+L_c, y1=y_curr+l_c, 
                          line=dict(color="DarkSlateGrey", width=2), fillcolor="ForestGreen")
            count += 1
            y_curr += l_c

    fig.update_layout(
        title=f"Chargement : {count} / {int(qte)} {contenant_type}",
        xaxis=dict(title="Longueur Camion (m)", range=[-0.2, L_v + 0.5]),
        yaxis=dict(title="Largeur (m)", range=[-0.2, l_v + 0.5], scaleanchor="x", scaleratio=1),
        width=700, height=400
    )
    return fig
