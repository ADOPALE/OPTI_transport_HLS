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
    Version Robuste : Détecte les colonnes par mots-clés et nettoie les données.
    Prépare le dictionnaire de missions pour la simulation.
    """
    # 1. Nettoyage initial : on enlève les espaces invisibles dans les noms de colonnes
    df_flux.columns = [str(c).strip() for c in df_flux.columns]
    
    # 2. Fonction interne de détection intelligente des colonnes
    def trouver_col(mots_cles_possibles):
        for c in df_flux.columns:
            if any(mot.lower() in c.lower() for mot in mots_cles_possibles):
                return c
        return None

    # Mappage des colonnes critiques
    col_nature = trouver_col(["Nature du flux", "obligation", "type de flux"])
    col_depart = trouver_col(["Point de départ", "origine", "provenance"])
    col_dest = trouver_col(["Point de destination", "destination", "arrivée"])
    col_contenant = trouver_col(["Nature de contenant", "contenant", "support"])
    col_etat = trouver_col(["Plein / vide", "etat"])
    col_hygiene = trouver_col(["Sale / propre", "hygiene", "statut"])
    col_mixte = trouver_col(["Transport mixte", "mixte"])
    col_excl = trouver_col(["Règles d'exclusions", "exclusion"])
    col_cadence = trouver_col(["Cadence", "flux j+1"])
    col_urgence = trouver_col(["Urgence", "prioritaire"])
    col_h_dispo = trouver_col(["mise à disposition", "h_dispo", "dispo"])
    col_h_limite = trouver_col(["heure max", "h_limite", "limite"])

    # Mapping des jours (on cherche "Lundi", "Mardi", etc.)
    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    jours_map = {}
    for j in jours_nom:
        # On cherche une colonne qui contient le nom du jour (ex: "Quantité Lundi" ou juste "Lundi")
        col_trouvee = trouver_col([f"Quantité {j}", f"Qté {j}", j])
        if col_trouvee:
            jours_map[j] = col_trouvee

    # 3. Filtrage : On ne garde que les lignes qui ont un départ et une destination
    # Et on ignore les lignes totalement vides en bas de l'Excel
    df_propre = df_flux.dropna(subset=[col_depart, col_dest])
    
    # On initialise le dictionnaire final
    # IMPORTANT : Les clés doivent correspondre à ce que ta boucle de simulation attend
    missions_par_jour = {f"Quantité {j}": [] for j in jours_nom}

    # 4. Boucle de lecture des lignes
    for idx, row in df_propre.iterrows():
        # Sécurité : vérifier que la ligne n'est pas un titre ou un sous-total
        val_nature = str(row.get(col_nature, "")).lower()
        # On ne prend que les lignes qui contiennent "Volume" ou si la nature n'est pas précisée
        if "volume" not in val_nature and val_nature != "nan" and val_nature != "":
             continue

        # Extraction des horaires (avec valeurs par défaut si erreur)
        try:
            h_start = row[col_h_dispo].hour * 60 + row[col_h_dispo].minute
            h_end = row[col_h_limite].hour * 60 + row[col_h_limite].minute
        except:
            h_start, h_end = 360, 1200 # 06h00 à 20h00 par défaut

        # Mixité et Exclusions
        mixte_possible = str(row.get(col_mixte, "NON")).strip().upper() == "OUI"
        tag_compat = "MIXTE_OK" if mixte_possible else f"DEDIE_{idx}"
        exclusions = [x.strip().upper() for x in str(row.get(col_excl, "")).split(',') if x.strip()]

        # 5. Ventilation par jour
        for jour_nom, col_excel in jours_map.items():
            qte = pd.to_numeric(row[col_excel], errors='coerce')
            
            if qte > 0:
                missions_par_jour[f"Quantité {jour_nom}"].append({
                    "id_flux": idx,
                    "origine": str(row[col_depart]).strip().upper(),
                    "destination": str(row[col_dest]).strip().upper(),
                    "contenant": str(row[col_contenant]).strip().upper(),
                    "est_plein": "PLEIN" in str(row.get(col_etat, "PLEIN")).upper(),
                    "est_propre": "PROPRE" in str(row.get(col_hygiene, "PROPRE")).upper(),
                    "tag_compatibilite": tag_compat,
                    "exclusions": exclusions,
                    "quantite_totale": qte,
                    "fenetre_start": h_start,
                    "fenetre_end": h_end,
                    "cadence": str(row.get(col_cadence, "J0"))
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
