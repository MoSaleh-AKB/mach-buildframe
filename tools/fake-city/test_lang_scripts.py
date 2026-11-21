import importlib.util,sys
sp=importlib.util.spec_from_file_location("brain",sys.argv[1]);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
from types import SimpleNamespace as N
import inspect
def L(t):
    try:
        msg=m.Message(sender="citizen:x",text=t,need_id="n") 
    except Exception as e:
        msg=N(sender="citizen:x",text=t,need_id="n")
    return m._lang_of(msg)
for t,w in [("こんにちは、腰が痛いです","ja"),("আমার পিঠে ব্যথা।","bn"),("मेरी कमर में दर्द है","hi"),("நான் முதுகு வலி","ta"),("මගේ පිටුව රිදෙනවා","si"),("مجھے کمر میں درد ہے","ur"),("у меня болит спина","ru"),("мене болить спина і серце ї","uk"),("我背痛","zh"),("بمبو پشتم درد میکنه","fa"),("ظهري يؤلمني","ar")]:
    print(L(t),w)
