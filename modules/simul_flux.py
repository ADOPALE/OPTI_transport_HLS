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
    # 1. Nettoyage des noms de colonnes
    df_flux.columns = [str(c).strip() for c in df_flux.columns]
    
    # 2. On définit les jours (C'est là que les données se trouvent)
    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    missions_par_jour = {f"Quantité {j}": [] for j in jours_nom}

    # 3. Identification des colonnes par leur POSITION (Index)
    # Basé sur ton debug : 0=Départ, 1=Dest, 4=Contenant, 8=Mixte
    # Pour 'Volume', on va la chercher dynamiquement
    idx_dep = 0
    idx_dest = 1
    idx_cont = 4
    
    # On cherche l'index de la colonne qui contient "obligation" (la colonne verte sur ton image)
    idx_nature = next((i for i, c in enumerate(df_flux.columns) if "obligation" in c.lower()), 10)

    # 4. Parcours des lignes (on commence à la ligne 0 puisque tes titres sont en ligne 1)
    for idx, row in df_flux.iterrows():
        # On récupère les valeurs par position
        dep = str(row.iloc[idx_dep]).strip().upper()
        dest = str(row.iloc[idx_dest]).strip().upper()
        nature_val = str(row.iloc[idx_nature]).strip().lower()

        # SÉCURITÉ : On vérifie si c'est bien une ligne de calcul
        # Sur ton image, cette colonne contient "Volume"
        if "volume" not in nature_val:
            continue

        # 5. Extraction des quantités par jour
        for jour in jours_nom:
            # On cherche la colonne "Quantité Lundi", "Quantité Mardi", etc.
            col_nom = next((c for c in df_flux.columns if jour.lower() in c.lower() and "quantité" in c.lower()), None)
            
            if col_nom:
                qte = pd.to_numeric(row[col_nom], errors='coerce')
                
                if qte > 0:
                    missions_par_jour[f"Quantité {jour}"].append({
                        "id_flux": idx,
                        "origine": dep,
                        "destination": dest,
                        "contenant": str(row.iloc[idx_cont]).strip().upper(),
                        "quantite_totale": int(qte),
                        "est_plein": "PLEIN" in str(row.iloc[5]).upper(),
                        "est_propre": "PROPRE" in str(row.iloc[6]).upper(),
                        "fenetre_start": 360, # 06:00
                        "fenetre_end": 1200, # 20:00
                        "tag_compatibilite": "MIXTE_OK" if "OUI" in str(row.iloc[8]).upper() else f"DEDIE_{idx}",
                        "exclusions": []
                    })

    return missions_par_jour




def calculer_capacite_emport_finale(mission, vehicule_name, df_vehicules, df_contenants):
    # 1. Récupération des paramètres
    config = st.session_state.get("params_logistique", {})
    taux = config.get("securite_remplissage", 1.0) # Par défaut 100% si non défini
    if taux == 0: taux = 1.0 # Sécurité anti-zero

    # 2. Récupération des specs véhicule
    spec_v = df_vehicules[df_vehicules['Types'] == vehicule_name].iloc[0]
    
    # --- DEBUG INTERNE ---
    # On nettoie le nom du contenant pour la comparaison
    nom_contenant = mission['contenant'].strip().upper()
    
    # On cherche la colonne qui correspond au contenant dans le tableau véhicule
    col_compat = next((c for c in df_vehicules.columns if nom_contenant in c.upper()), None)
    
    # Si le véhicule ne peut pas porter ce contenant (marqué NON)
    if col_compat and str(spec_v[col_compat]).upper() == "NON":
        return 0

    # 3. Calcul de la capacité (Dimensions)
    try:
        spec_c = df_contenants[df_contenants['libellé'].str.upper() == nom_contenant].iloc[0]
        L_cam, l_cam = float(spec_v['dim longueur interne (m)']), float(spec_v['dim largeur interne (m)'])
        dim1, dim2 = float(spec_c['dim longueur (m)']), float(spec_c['dim largeur (m)'])
        
        capa_A = (L_cam // dim1) * (l_cam // dim2)
        capa_B = (L_cam // dim2) * (l_cam // dim1)
        capa_finale = max(capa_A, capa_B)
    except Exception as e:
        # Si le calcul géométrique échoue, on prend une valeur par défaut selon le type
        capa_finale = 1 

    return max(1, int(capa_finale * taux))




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
    if not missions_du_jour:
        return []

    # On récupère les types de véhicules disponibles
    v_selectionnes = df_vehicules['Types'].tolist()
    
    rotations_liste = []

    for m in missions_du_jour:
        # On cherche le véhicule qui a la plus grosse capacité pour cette mission
        meilleure_capa, meilleur_v = 0, None
        for v_name in v_selectionnes:
            capa = calculer_capacite_emport_finale(m, v_name, df_vehicules, df_contenants)
            if capa > meilleure_capa:
                meilleure_capa, meilleur_v = capa, v_name
        
        # Si on a trouvé un véhicule capable
        if meilleur_v and meilleure_capa > 0:
            qte_restante = m['quantite_totale']
            while qte_restante > 0:
                emport = min(qte_restante, meilleure_capa)
                duree = calculer_duree_rotation(m, meilleur_v, emport, df_vehicules, matrice_duree)
                rotations_liste.append({"duree": duree, "fin": m['fenetre_end']})
                qte_restante -= emport
        else:
            # DEBUG : Afficher si une mission ne trouve aucun camion
            # st.warning(f"Aucun véhicule compatible pour {m['contenant']}")
            pass

    # Lissage RH : On regroupe les rotations en journées de 7h30 (450 min)
    if not rotations_liste: return []
    
    rotations_liste.sort(key=lambda x: x['fin'])
    postes = []
    current_poste_duree = 0
    for rot in rotations_liste:
        if current_poste_duree + rot["duree"] <= 450:
            current_poste_duree += rot["duree"]
        else:
            postes.append(current_poste_duree)
            current_poste_duree = rot["duree"]
    if current_poste_duree > 0: postes.append(current_poste_duree)

    return postes
