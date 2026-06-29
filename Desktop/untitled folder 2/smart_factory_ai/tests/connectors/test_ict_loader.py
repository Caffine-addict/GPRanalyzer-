from connectors.ict.loader import ICTLoader

loader = ICTLoader(
    "data/raw/ict"
)

df = loader.load_all()

print(df.shape)

print(df.head())

print(
    "\nBoards:",
    df["file_name"].nunique()
)
