import pandas as pd
import streamlit as st

def convertir_temps_manutention(valeur):
    """
    Convertit une valeur Excel (datetime.time, Timestamp ou str) en secondes.
    Indispensable pour traiter les colonnes de manutention (ex: 00:00:25).
    """
    if isinstance(valeur, pd.Timestamp) or hasattr(valeur, 'hour'):
        return valeur.hour * 3600 + valeur.minute * 60 + valeur.second
    elif isinstance(valeur, str):
        try:
            # Gère le format HH:MM:SS ou MM:SS
            parts = list(map(int, valeur.split(':')))
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            elif len(parts) == 2:
                return parts[0] * 60 + parts[1]
        except:
            return 0.0
    return float(valeur) if pd.notnull(valeur) else 0.0

def preparer_missions_unifiees(df_flux):
    """
    Transforme le flux Excel en missions avec gestion des exclusions,
    du transport mixte et nettoyage automatique des noms de colonnes.
    """
    import pandas as pd
    
    # --- NETTOYAGE DES COLONNES ---
    # Supprime les espaces en début/fin de nom de colonne (souvent invisible dans Excel)
    df_flux.columns = [str(c).strip() for c in df_flux.columns]
    
    # Définition des noms de colonnes attendus
    cols_theoriques = {
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

    # --- LOGIQUE DE FALLBACK (SÉCURITÉ) ---
    # Si le nom exact n'est pas trouvé, on cherche une colonne qui contient le mot-clé principal
    cols = {}
    for cle, nom_long in cols_theoriques.items():
        if nom_long in df_flux.columns:
            cols[cle] = nom_long
        else:
            # Recherche par mot-clé si le nom long a été modifié
            mots_cles = {
                "nature": "Nature du flux",
                "depart": "départ",
                "dest": "destination",
                "conteneur": "contenant",
                "h_dispo": "disposition",
                "h_limite": "limite"
            }
            mot = mots_cles.get(cle, cle)
            trouve = [c for c in df_flux.columns if mot.lower() in c.lower()]
            if trouve:
                cols[cle] = trouve[0]
            else:
                # Si vraiment introuvable, on garde le nom théorique pour l'erreur explicite
                cols[cle] = nom_long

    jours_cols = ["Quantité Lundi", "Quantité Mardi", "Quantité Mercredi", 
                  "Quantité Jeudi", "Quantité Vendredi", "Quantité Samedi", "Quantité Dimanche"]

    # Vérification finale de la colonne critique
    if cols["nature"] not in df_flux.columns:
        raise KeyError(f"La colonne '{cols['nature']}' est introuvable. Vérifiez votre fichier Excel.")

    # Filtrage des lignes de type "Volume"
    df_vol = df_flux[df_flux[cols["nature"]].astype(str).str.contains("Volume", case=False, na=False)].copy()
    missions_par_jour = {j: [] for j in jours_cols}

    for idx, row in df_vol.iterrows():
        # Gestion des horaires (conversion en minutes depuis minuit)
        try:
            h_start = row[cols["h_dispo"]].hour * 60 + row[cols["h_dispo"]].minute
            h_end = row[cols["h_limite"]].hour * 60 + row[cols["h_limite"]].minute
        except:
            h_start, h_end = 360, 1200 # Valeurs par défaut (06h00 - 20h00)

        # Logique de mixité et compatibilité
        mixte_possible = str(row[cols["mixte"]]).strip().upper() == "OUI"
        tag_compatibilite = "MIXTE_OK" if mixte_possible else f"DEDIE_{idx}"
        
        # Nettoyage de la liste des exclusions
        exclusions = [x.strip().upper() for x in str(row.get(cols["regle_excl"], "")).split(',') if x.strip()]

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
                    "mixte_possible": mixte_possible,
                    "tag_compatibilite": tag_compatibilite,
                    "exclusions": exclusions,
                    "cadence": str(row[cols["cadence"]]).strip(),
                    "est_urgent": "OUI" in str(row.get(cols["urgence"], "")).upper(),
                    "quantite_totale": qte,
                    "fenetre_start": h_start,
                    "fenetre_end": h_end
                })

    return missions_par_jour



def calculer_capacite_emport_finale(mission, vehicule_name, df_vehicules, df_contenants):
    """
    Calcule la capacité réelle via Tetris (rangées/colonnes) avec pivotement à 90°.
    Vérifie la compatibilité technique (OUI/NON) et la charge utile.
   
    """
    config = st.session_state.get("params_logistique", {"securite_remplissage": 0.85})
    taux = config["securite_remplissage"]

    spec_v = df_vehicules[df_vehicules['Types'] == vehicule_name].iloc[0]
    # Vérification compatibilité (OUI/NON dans le tableau véhicules)
    if spec_v.get(mission['contenant'], "NON") == "NON":
        return 0

    spec_c = df_contenants[df_contenants['libellé'] == mission['contenant']].iloc[0]
    
    # Dimensions
    L_cam, l_cam = spec_v['dim longueur interne (m)'], spec_v['dim largeur interne (m)']
    dim1, dim2 = spec_c['dim longueur (m)'], spec_c['dim largeur (m)']

    # Tetris avec Pivot
    capa_A = (L_cam // dim1) * (l_cam // dim2)
    capa_B = (L_cam // dim2) * (l_cam // dim1)
    meilleur_sol = max(capa_A, capa_B)

    # Masse
    poids_u = spec_c['Poids plein (kg)'] if mission['est_plein'] else spec_c['Poids vide (kg)']
    cu_kg = float(str(spec_v['Poids max chargement']).upper().replace('T', '').replace(',', '.').strip()) * 1000
    
    capa_poids = int(cu_kg // poids_u) if poids_u > 0 else meilleur_sol

    return int(min(meilleur_sol, capa_poids) * taux)

def calculer_duree_rotation(mission, vehicule_name, qte, df_vehicules, matrice_duree):
    """
    Calcule le cycle complet : Mise à quai + Manutention + Trajet A/R.
   
    """
    spec_v = df_vehicules[df_vehicules['Types'] == vehicule_name].iloc[0]
    
    # Remplace tes lignes A par :
    t_mise_a_quai = convertir_en_secondes(spec_v['Temps de mise à quai - manœuvre, contact/admin min (minutes)']) / 60
    t_unit_sec = convertir_en_secondes(spec_v['Manutention on sans quai (minutes / contenants)'])
        
    manut_min = (t_quai_min * 2) + ((t_unit_sec * qte * 2) / 60)
    
    try:
        trajet = matrice_duree.loc[mission['origine'], mission['destination']] * 2
    except:
        trajet = 60 # Défaut
        
    return manut_min + trajet

def generer_rotations_physiques(missions, df_vehicules, df_contenants):
    """
    Éclate les volumes en trajets selon le meilleur véhicule compatible.
    """
    rotations = []
    vehicules_autorises = st.session_state["params_logistique"]["vehicules_selectionnes"]

    for m in missions:
        # Trouver le véhicule le plus performant pour ce flux
        meilleure_capa, meilleur_v = 0, None
        for v_name in vehicules_autorises:
            c = calculer_capacite_emport_finale(m, v_name, df_vehicules, df_contenants)
            if c > meilleure_capa:
                meilleure_capa, meilleur_v = c, v_name
        
        if meilleure_capa == 0:
            continue

        qte_reste = m['quantite_totale']
        while qte_reste > 0:
            emport = min(qte_reste, meilleure_capa)
            rotations.append({
                "mission_origine": m, "vehicule": meilleur_v, "qte_chargee": emport,
                "fenetre_start": m['fenetre_start'], "fenetre_end": m['fenetre_end']
            })
            qte_reste -= emport
    return rotations

def lisser_et_assigner(rotations, df_vehicules, matrice_duree, config):
    """
    Ordonne les trajets et les regroupe en journées de 7h30 (450 min).
    """
    rotations.sort(key=lambda x: x['fenetre_end'])
    postes = []
    h_prise = config['rh']['h_prise_min'].hour * 60 + config['rh']['h_prise_min'].minute
    duree_max = config['rh']['amplitude_totale']

    current_poste = {"duree": 0, "tours": []}
    
    for rot in rotations:
        d = calculer_duree_rotation(rot['mission_origine'], rot['vehicule'], rot['qte_chargee'], df_vehicules, matrice_duree)
        
        if current_poste["duree"] + d <= duree_max:
            current_poste["tours"].append(rot)
            current_poste["duree"] += d
        else:
            postes.append(current_poste)
            current_poste = {"duree": d, "tours": [rot]}
            
    if current_poste["tours"]:
        postes.append(current_poste)
    return postes

def simuler_tournees_quotidiennes(missions_du_jour, df_vehicules, df_contenants, matrice_duree):
    """
    Moteur principal : Groupage -> Rotations -> Lissage -> Postes RH.
    """
    config = st.session_state["params_logistique"]
    resultat_final = []
    
    # 1. Groupage par compatibilité
    groupes = {}
    for m in missions_du_jour:
        cle = (m['est_propre'], m['tag_compatibilite'])
        groupes.setdefault(cle, []).append(m)

    # 2. Simulation par groupe
    for missions in groupes.values():
        rotations = generer_rotations_physiques(missions, df_vehicules, df_contenants)
        postes = lisser_et_assigner(rotations, df_vehicules, matrice_duree, config)
        resultat_final.extend(postes)

    return resultat_final


def convertir_en_secondes(valeur):
    """Transforme 00:00:25 en 25 secondes (float)"""
    if hasattr(valeur, 'hour'): # Si c'est un objet temps
        return valeur.hour * 3600 + valeur.minute * 60 + valeur.second
    try:
        return float(valeur) # Si c'est déjà un nombre
    except:
        return 0.0
