import pandas as pd
import streamlit as st
import math

def convertir_temps_manutention(valeur):
    """
    Convertit une valeur Excel (datetime.time, str ou float) en minutes.
    Indispensable pour traiter les colonnes comme '00:00:25'.
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
    """
    Transforme le flux brut en dictionnaire de missions par jour.
    Nettoie les colonnes et filtre les lignes 'Volume'.
    """
    # Nettoyage des noms de colonnes (enlève les espaces invisibles)
    df_flux.columns = [str(c).strip() for c in df_flux.columns]
    
    cols = {
        "nature": "Nature du flux (les tournées sont elles à prévoir avec une obligation de transport ou une obligation de passage ?)",
        "depart": "Point de départ",
        "dest": "Point de destination",
        "conteneur": "Nature de contenant",
        "etat": "Plein / vide",
        "hygiene": "Sale / propre",
        "mixte": "Transport mixte possible (OUI / NON)",
        "regle_excl": "Règles d'exclusions si transport mixte",
        "cadence": "Cadence de prod (nb de chariot par durée/ J1 - tous les chariots sont fait la veille et peuvent partir en même temps ou aléat)",
        "urgence": "Urgence / flux prioritaire (Oui/Non)",
        "h_dispo": "Heure de mise à disposition min départ",
        "h_limite": "Heure max de livraison à la destination"
    }
    
    jours_cols = ["Quantité Lundi", "Quantité Mardi", "Quantité Mercredi", 
                  "Quantité Jeudi", "Quantité Vendredi", "Quantité Samedi", "Quantité Dimanche"]

    # Filtrage : On accepte 'Volume' peu importe la casse ou le pluriel
    mask_vol = df_flux[cols["nature"]].astype(str).str.contains("Volume", case=False, na=False)
    df_vol = df_flux[mask_vol].copy()
    
    missions_par_jour = {j: [] for j in jours_cols}

    for idx, row in df_vol.iterrows():
        try:
            h_start = row[cols["h_dispo"]].hour * 60 + row[cols["h_dispo"]].minute
            h_end = row[cols["h_limite"]].hour * 60 + row[cols["h_limite"]].minute
        except:
            h_start, h_end = 360, 1200 # 06h00 - 20h00 par défaut

        mixte_possible = str(row[cols["mixte"]]).strip().upper() == "OUI"
        tag_compatibilite = "MIXTE_OK" if mixte_possible else f"DEDIE_{idx}"
        exclusions = [x.strip().upper() for x in str(row[cols["regle_excl"]]).split(',') if x.strip()]

        for jour in jours_cols:
            qte = pd.to_numeric(row[jour], errors='coerce')
            if qte > 0:
                missions_par_jour[jour].append({
                    "id_flux": idx,
                    "origine": str(row[cols["depart"]]).strip().upper(),
                    "destination": str(row[cols["dest"]]).strip().upper(),
                    "contenant": str(row[cols["conteneur"]]).strip().upper(),
                    "est_plein": "PLEIN" in str(row[cols["etat"]]).upper(),
                    "est_propre": "PROPRE" in str(row[cols["hygiene"]]).upper(),
                    "tag_compatibilite": tag_compatibilite,
                    "exclusions": exclusions,
                    "quantite_totale": qte,
                    "fenetre_start": h_start,
                    "fenetre_end": h_end
                })
    return missions_par_jour

def calculer_capacite_emport_finale(mission, vehicule_name, df_vehicules, df_contenants):
    """Calcule la capacité réelle via Tetris (pivotement) et Poids."""
    # Sécurité : Si le paramètre n'existe pas, on met 100% (1.0)
    config = st.session_state.get("params_logistique", {})
    taux = config.get("securite_remplissage", 1.0)

    spec_v = df_vehicules[df_vehicules['Types'] == vehicule_name].iloc[0]
    
    # Vérification OUI/NON dans le tableau véhicules
    if spec_v.get(mission['contenant'], "NON") == "NON":
        return 0

    spec_c = df_contenants[df_contenants['libellé'] == mission['contenant']].iloc[0]
    
    L_cam, l_cam = spec_v['dim longueur interne (m)'], spec_v['dim largeur interne (m)']
    dim1, dim2 = spec_c['dim longueur (m)'], spec_c['dim largeur (m)']

    # Tetris avec Pivot 90°
    capa_A = (L_cam // dim1) * (l_cam // dim2)
    capa_B = (L_cam // dim2) * (l_cam // dim1)
    meilleur_sol = max(capa_A, capa_B)

    # Masse
    poids_u = spec_c['Poids plein (kg)'] if mission['est_plein'] else spec_c['Poids vide (kg)']
    try:
        cu_kg = float(str(spec_v['Poids max chargement']).upper().replace('T', '').replace(',', '.').strip()) * 1000
    except:
        cu_kg = 10000.0 # Par défaut 10 tonnes
    
    capa_poids = int(cu_kg // poids_u) if poids_u > 0 else meilleur_sol

    return int(min(meilleur_sol, capa_poids) * taux)

def calculer_duree_rotation(mission, vehicule_name, qte, df_vehicules, matrice_duree):
    """Temps total : Quai + Manutention + Trajet A/R."""
    spec_v = df_vehicules[df_vehicules['Types'] == vehicule_name].iloc[0]
    
    # Correction format temps HH:MM:SS
    t_quai_min = convertir_temps_manutention(spec_v['Temps de mise à quai - manœuvre, contact/admin min (minutes)'])
    t_unit_sec = convertir_temps_manutention(spec_v['Manutention on sans quai (minutes / contenants)'])
    
    # Cycle manutention (Aller + Retour)
    manut_totale = (t_quai_min * 2) + ((t_unit_sec * qte * 2)) 
    
    try:
        # On essaie de récupérer le trajet spécifique, sinon valeur par défaut
        trajet = matrice_duree.loc[mission['origine'], mission['destination']] * 2
    except:
        trajet = 60.0
        
    return manut_totale + trajet

def simuler_tournees_quotidiennes(missions_du_jour, df_vehicules, df_contenants, matrice_duree):
    """Moteur de simulation : transforme missions en rotations puis en postes RH."""
    if not missions_du_jour:
        return []

    config = st.session_state.get("params_logistique", {"rh": {"amplitude_totale": 450}})
    # On récupère les véhicules sélectionnés par l'utilisateur
    v_selectionnes = config.get("vehicules_selectionnes", df_vehicules['Types'].tolist())
    
    rotations_liste = []

    # 1. Éclatement des volumes en rotations physiques
    for m in missions_du_jour:
        # Trouver le véhicule avec la meilleure capacité
        meilleure_capa, meilleur_v = 0, None
        for v_name in v_selectionnes:
            capa = calculer_capacite_emport_finale(m, v_name, df_vehicules, df_contenants)
            if capa > meilleure_capa:
                meilleure_capa, meilleur_v = capa, v_name
        
        if meilleure_capa <= 0:
            continue

        # Création des trajets nécessaires pour tout transporter
        qte_restante = m['quantite_totale']
        while qte_restante > 0:
            emport = min(qte_restante, meilleure_capa)
            duree = calculer_duree_rotation(m, meilleur_v, emport, df_vehicules, matrice_duree)
            
            rotations_liste.append({
                "duree": duree,
                "fenetre_end": m['fenetre_end'],
                "label": f"{emport} {m['contenant']} ({m['origine']}->{m['destination']})"
            })
            qte_restante -= emport

    # 2. Lissage RH (Assignation aux postes de 7h30)
    # On trie par heure limite (First Expiring First)
    rotations_liste.sort(key=lambda x: x['fenetre_end'])
    
    postes = []
    current_poste = {"duree_cumulee": 0, "missions": []}
    amplitude_max = config.get("rh", {}).get("amplitude_totale", 450) # 450min = 7h30

    for rot in rotations_liste:
        if current_poste["duree_cumulee"] + rot["duree"] <= amplitude_max:
            current_poste["missions"].append(rot)
            current_poste["duree_cumulee"] += rot["duree"]
        else:
            postes.append(current_poste)
            current_poste = {"duree_cumulee": rot["duree"], "missions": [rot]}
    
    if current_poste["missions"]:
        postes.append(current_poste)

    return postes
