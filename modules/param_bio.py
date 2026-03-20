def show_biologie_page():
    st.title("🧪 Paramétrage des Passages Biologie")

    if "data" not in st.session_state:
        st.warning("⚠️ Veuillez d'abord importer un fichier Excel.")
        return

    data = st.session_state["data"]
    df_sites = data["accessibilite_sites"]
    
    # Paramètres globaux
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        duree_max = st.number_input("Durée max tournée (min)", value=200)
    with col_g2:
        temps_coll = st.number_input("Temps de collecte (min)", value=10)

    st.divider()
    st.subheader("🏥 Sélection et configuration des sites")

    # Initialisation du dictionnaire de configuration
    current_sites_config = {}

    for index, row in df_sites.iterrows():
        site_name = row['site']
        if site_name == "HLS": continue 
        
        # Création d'une ligne avec checkbox et expander
        cols = st.columns([1, 4])
        
        # 1. Possibilité de cocher/décocher le site
        is_active = cols[0].checkbox("Inclure", value=True, key=f"check_{site_name}")
        
        if is_active:
            with cols[1].expander(f"📍 {site_name}", expanded=True):
                c1, c2 = st.columns([3, 1])
                with c1:
                    # Slider pour la plage horaire
                    # On peut pré-remplir avec les valeurs de l'Excel si vous les avez extraites
                    res = st.select_slider(
                        f"Plage horaire",
                        options=range(0, 1441, 15),
                        value=(480, 1080), # 8h - 18h par défaut
                        format_func=lambda x: f"{x//60:02d}:{x%60:02d}",
                        key=f"slide_{site_name}"
                    )
                with c2:
                    freq = st.number_input(f"Fréquence", min_value=1, value=5, key=f"freq_{site_name}")

                # On n'ajoute à la config que si le site est coché
                current_sites_config[site_name] = {
                    'open': res[0],
                    'close': res[1],
                    'freq': freq
                }
        else:
            cols[1].info(f"❄️ {site_name} est exclu de la simulation.")

    st.subheader("🚐 Contraintes RH & Chauffeurs")
    c1, c2, c3 = st.columns(3)
    with c1:
        amplitude_poste = st.number_input("Durée du poste (min)", value=450, help="7h30 = 450 minutes")
    with c2:
        pause_dej = st.number_input("Pause déjeuner (min)", value=30)
    with c3:
        temps_releve = st.number_input("Temps de relève (min)", value=15, help="Temps entre deux chauffeurs sur le même véhicule")

    # Ajouter ces valeurs dans le dictionnaire de sauvegarde
    if st.button("💾 Enregistrer la configuration"):
        st.session_state["biologie_config"] = {
            "duree_max": duree_max,
            "temps_collecte": temps_coll,
            "sites": current_sites_config,
            "rh": {
                "amplitude": amplitude_poste,
                "pause": pause_dej,
                "releve": temps_releve
            }
        }
        st.success(f"Configuration enregistrée : {len(current_sites_config)} sites actifs.")
