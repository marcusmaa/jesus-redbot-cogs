from .jesus_dash import JesusDash


async def setup(bot):
    await bot.add_cog(JesusDash(bot))
