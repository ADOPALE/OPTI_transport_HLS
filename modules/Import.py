import streamlit as st
import pandas as pd

def show_import():
    st.header("⚙️ Importation des données")
    uploaded_file = st.file_uploader("Charger le fichier Excel de paramétrage", type=["xlsx"])
    
    if uploaded_file:
        df = pd.read_excel(uploaded_file)
        st.success("Fichier chargé !")
        st.dataframe(df.head()) # Aperçu
        # Stockage dans la session pour les autres pages
        st.session_state['data_params'] = df
