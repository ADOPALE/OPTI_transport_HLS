import streamlit as st 
import folium
from streamlit_folium import st_folium
import pandas as pd


def show_biologie_page():
    st.title("🧪 Paramétrage des Passages Biologie")

    if "data" not in st.session_state:
        st.warning("⚠️ Veuillez d'abord importer un fichier Excel.")
        return

    # 1. Récupération des données m_flux
    df_flux = st.session_state["data"]["m_flux"].copy()
    
    # On identifie la colonne K (index 10) pour filtrer sur "Fréquences"
    # On utilise .strip() pour ignorer les espaces invisibles et .lower() pour la casse
    col_k = df_flux.columns[10]
    df_freq = df_flux[df_flux[col_k].astype(str).str.lower().str.strip().str.contains("fréquence", na=False)]

    if df_freq.empty:
        st.error(f"❌ Aucune ligne avec la mention 'Fréquences' trouvée dans la colonne K.")
        return

    # 2. Vérification de l'unicité des sites (Colonne A = Index 0)
    col_a = df_flux.columns[0]
    sites_counts = df_freq[col_a].value_counts()
    doublons = sites_counts[sites_counts > 1].index.tolist()

    if doublons:
        st.error(f"❌ Doublons détectés dans 'M flux' pour les sites : {', '.join(doublons)}")
        st.info("Chaque site ne doit avoir qu'une seule ligne 'Fréquences'.")
        return

    # Paramètres globaux
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        duree_max = st.number_input("Durée max tournée (min)", value=120)
    with col_g2:
        temps_coll = st.number_input("Temps de collecte (min)", value=10)

    st.divider()
    st.subheader("🏥 Configuration des sites")

    current_sites_config = {}

    for _, row in df_freq.iterrows():
        site_name = row.iloc[0] # Colonne A
        if site_name == "HLS": continue 

        # 3. Conversion des heures Excel (S=18, T=19) et Fréquence (W=22)
        try:
            def extraire_minutes(valeur, defaut_min):
                if pd.isna(valeur) or str(valeur).strip() == "":
                    return defaut_min
                
                # Cas 1 : C'est déjà un objet de temps (HH:MM:SS)
                if hasattr(valeur, 'hour'): 
                    return valeur.hour * 60 + valeur.minute
                
                # Cas 2 : C'est une chaîne de caractères "08:30"
                if isinstance(valeur, str) and ":" in valeur:
                    h, m = map(int, valeur.split(':')[:2])
                    return h * 60 + m
                
                # Cas 3 : C'est le nombre décimal Excel (ex: 0.333)
                try:
                    return int(float(valeur) * 1440)
                except:
                    return defaut_min

            # Application aux colonnes S(18), T(19) et W(22)
            h_ouvert = extraire_minutes(row.iloc[18], 480)
            h_ferme = extraire_minutes(row.iloc[19], 1080)
            
            # Pour la fréquence (W), on s'assure d'avoir un entier
            val_w = row.iloc[22]
            if pd.isna(val_w) or str(val_w).strip() == "":
                nb_passages = 3
            else:
                nb_passages = int(float(val_w))

        except Exception as e:
            # Si vraiment ça plante encore, on affiche l'erreur pour comprendre
            st.error(f"Erreur sur le site {site_name} : {e}")
            h_ouvert, h_ferme, nb_passages = 480, 1080, 3

        cols = st.columns([1, 4])
        is_active = cols[0].checkbox("Inclure", value=True, key=f"check_{site_name}")
        
        if is_active:
            with cols[1].expander(f"📍 {site_name}", expanded=True):
                c1, c2 = st.columns([3, 1])
                with c1:
                    # Slider calé sur les valeurs Excel (arrondi à 15min)
                    res = st.select_slider(
                        f"Plage horaire",
                        options=range(0, 1441, 15),
                        value=(h_ouvert - (h_ouvert % 15), h_ferme - (h_ferme % 15)),
                        format_func=lambda x: f"{x//60:02d}:{x%60:02d}",
                        key=f"slide_{site_name}"
                    )
                with c2:
                    freq = st.number_input(f"Passages", min_value=1, value=nb_passages, key=f"freq_{site_name}")

                current_sites_config[site_name] = {'open': res[0], 'close': res[1], 'freq': freq}

    st.subheader("🚐 Contraintes RH")
    c1, c2, c3 = st.columns(3)
    with c1:
        amplitude_poste = st.number_input("Durée poste (min)", value=450)
    with c2:
        pause_dej = st.number_input("Pause (min)", value=30)
    with c3:
        temps_releve = st.number_input("Relève (min)", value=15)

    if st.button("💾 Enregistrer la configuration", use_container_width=True):
        st.session_state["biologie_config"] = {
            "duree_max": duree_max,
            "temps_collecte": temps_coll,
            "sites": current_sites_config,
            "rh": {"amplitude": amplitude_poste, "pause": pause_dej, "releve": temps_releve}
        }
        st.success(f"Configuration enregistrée pour {len(current_sites_config)} sites.")
