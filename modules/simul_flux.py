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
