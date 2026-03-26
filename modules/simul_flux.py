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
    """
    Transforme le flux brut en missions par jour avec détection flexible des colonnes.
    """
    # Nettoyage global des noms de colonnes pour éviter les erreurs d'espaces
    df_flux.columns = [str(c).strip() for c in df_flux.columns]
    
    # Cartographie des colonnes avec recherche par mot-clé en cas d'échec
    champs_attendus = {
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
    
    cols = {}
    for cle, nom_long in champs_attendus.items():
        if nom_long in df_flux.columns:
            cols[cle] = nom_long
        else:
            # Recherche d'une colonne contenant un mot-clé si le nom exact manque
            trouve = [c for c in df_flux.columns if cle.replace('_', ' ') in c.lower() or nom_long[:15].lower() in c.lower()]
            cols[cle] = trouve[0] if trouve else nom_long

    jours_cols = ["Quantité Lundi", "Quantité Mardi", "Quantité Mercredi", 
                  "Quantité Jeudi", "Quantité Vendredi", "Quantité Samedi", "Quantité Dimanche"]

    # Filtrage des lignes "Volume" (insensible à la casse)
    mask_vol = df_flux[cols["nature"]].astype(str).str.contains("Volume", case=False, na=False)
    df_vol = df_flux[mask_vol].copy()
    
    missions_par_jour = {j: [] for j in jours_cols}

    for idx, row in df_vol.iterrows():
        try:
            h_start = row[cols["h_dispo"]].hour * 60 + row[cols["h_dispo"]].minute
            h_end = row[cols["h_limite"]].hour * 60 + row[cols["h_limite"]].minute
        except:
            h_start, h_end = 360, 1200 

        mixte_possible = str(row[cols["mixte"]]).strip().upper() == "OUI"
        tag_compatibilite = "MIXTE_OK" if mixte_possible else f"DEDIE_{idx}"
        exclusions = [x.strip().upper() for x in str(row.get(cols["regle_excl"], "")).split(',') if x.strip()]

        for jour in jours_cols:
            if jour in df_flux.columns:
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
