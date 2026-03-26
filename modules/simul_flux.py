def preparer_missions_unifiees(df_flux):
    """
    Transforme le flux Excel en missions avec gestion fine des exclusions 
    et du transport mixte.
    """
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

    df_vol = df_flux[df_flux[cols["nature"]].astype(str).str.contains("Volume", case=False, na=False)].copy()
    missions_par_jour = {j: [] for j in jours_cols}

    for idx, row in df_vol.iterrows():
        # Gestion des horaires
        try:
            h_start = row[cols["h_dispo"]].hour * 60 + row[cols["h_dispo"]].minute
            h_end = row[cols["h_limite"]].hour * 60 + row[cols["h_limite"]].minute
        except:
            h_start, h_end = 360, 1200 

        # Logique de mixité
        mixte_possible = str(row[cols["mixte"]]).strip().upper() == "OUI"
        # Si mixte impossible, on crée un tag unique pour forcer le camion dédié
        tag_compatibilite = "MIXTE_OK" if mixte_possible else f"DEDIE_{idx}"
        
        # Liste des exclusions (ex: "Sale, Déchets") nettoyée
        exclusions = [x.strip().upper() for x in str(row[cols["regle_excl"]]).split(',') if x.strip()]

        for jour in jours_cols:
            qte = pd.to_numeric(row[jour], errors='coerce')
            if qte > 0:
                mission = {
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
                    "est_urgent": "OUI" in str(row[cols["urgence"]]).upper(),
                    "quantite_totale": qte,
                    "fenetre_start": h_start,
                    "fenetre_end": h_end
                }
                missions_par_jour[jour].append(mission)

    return missions_par_jour




def calculer_capacite_emport_finale(mission, vehicule_name, df_vehicules, df_contenants):
    """
    FONCTION DE RÉFÉRENCE : Calcule la capacité maximale réelle.
    Remplace les approches simplistes par un calcul de rangées/colonnes avec pivotement.
    """
    config = st.session_state["params_logistique"]
    taux_remplissage = config["securite_remplissage"]

    # 1. Récupération des dimensions et contraintes
    spec_v = df_vehicules[df_vehicules['Types'] == vehicule_name].iloc[0]
    spec_c = df_contenants[df_contenants['libellé'] == mission['contenant']].iloc[0]

    # --- VERIFICATION TECHNIQUE PREALABLE ---
    # Si le véhicule n'est pas équipé pour ce contenant (ton tableau OUI/NON)
    if spec_v.get(mission['contenant'], "NON") == "NON":
        return 0

    # 2. Dimensions internes et unitaires
    L_cam, l_cam = spec_v['dim longueur interne (m)'], spec_v['dim largeur interne (m)']
    dim1, dim2 = spec_c['dim longueur (m)'], spec_c['dim largeur (m)']

    # 3. LE TETRIS : Test des deux orientations
    # Orientation A : Longueur contenant sur Longueur camion
    capa_A = (L_cam // dim1) * (l_cam // dim2)
    # Orientation B : Largeur contenant sur Longueur camion (Pivot 90°)
    capa_B = (L_cam // dim2) * (l_cam // dim1)
    
    meilleur_sol = max(capa_A, capa_B)

    # 4. LA MASSE : Vérification du poids max
    poids_u = spec_c['Poids plein (kg)'] if mission['est_plein'] else spec_c['Poids vide (kg)']
    cu_kg = float(str(spec_v['Poids max chargement']).upper().replace('T', '').replace(',', '.').strip()) * 1000
    
    capa_poids = int(cu_kg // poids_u) if poids_u > 0 else meilleur_sol

    # 5. SYNTHÈSE : On prend le plus restrictif des deux (Sol vs Poids)
    # On applique le taux de sécurité (ex: 85%) sur la capacité physique
    resultat_final = int(min(meilleur_sol, capa_poids) * taux_remplissage)

    return max(0, resultat_final)




def calculer_duree_rotation(mission, vehicule_name, qte_a_transporter, df_vehicules, matrice_duree):
    """
    Calcule le temps total d'un aller-retour (Chargement + Trajet + Déchargement + Retour).
    """
    spec_v = df_vehicules[df_vehicules['Types'] == vehicule_name].iloc[0]
    
    # A. Temps de manutention (en secondes, converti en minutes)
    t_mise_a_quai = 10 # Valeur par défaut si format Excel complexe, sinon extraire
    t_unit_sec = spec_v['Manutention on sans quai (minutes / contenants)'] # On suppose secondes vu les chiffres
    
    # On calcule le temps de chargement + déchargement
    manutention_totale = (t_mise_a_quai * 2) + ((t_unit_sec * qte_a_transporter * 2) / 60)
    
    # B. Temps de trajet (Aller + Retour)
    # On récupère la durée entre l'origine et la destination dans ta matrice_duree
    duree_trajet_aller = matrice_duree.loc[mission['origine'], mission['destination']]
    duree_trajet_retour = matrice_duree.loc[mission['destination'], mission['origine']]
    
    return manutention_totale + duree_trajet_aller + duree_trajet_retour
